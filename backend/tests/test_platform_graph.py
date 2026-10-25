"""Behavioral tests for bounded human turns and truthful report aggregation."""

import copy

import pytest
import pytest_asyncio

from app.platform.graph import (
    diagnosis_graph,
    generate_questions,
    interview_graph,
    parsing_graph,
    report_graph,
    report_numbers,
)
from app.platform.runtime import AIError


@pytest_asyncio.fixture
async def snapshot():
    prepared = await parsing_graph.ainvoke({
        "raw_text": "张三\nPython 后端工程师\n负责订单平台的接口、数据持久化、缓存与故障排查。", "configs": {}, "demo": True,
    })
    generated = await generate_questions({**prepared, "demo": True})
    return {"questions": generated["questions"], "parsed": prepared["parsed"], "resume_score": 60,
            "demo": True, "messages": [], "evaluations": [], "main_index": -1, "follow_up_count": 0,
            "current_question": None, "status": "ready"}


@pytest.mark.asyncio
async def test_graph_stops_after_question_and_bounds_followups(snapshot):
    original = copy.deepcopy(snapshot)
    state = await interview_graph.ainvoke({"snapshot": snapshot, "configs": {}, "command": {"type": "start"}})
    assert snapshot == original
    assert len(state["snapshot"]["messages"]) == 1
    for expected_followups in (1, 2, 0):
        state = await interview_graph.ainvoke({"snapshot": state["snapshot"], "configs": {},
                                              "command": {"type": "answer", "content": "不知道"}})
        assert state["snapshot"]["follow_up_count"] == expected_followups
    assert state["snapshot"]["main_index"] == 1
    assert len(state["snapshot"]["evaluations"]) == 3
    assert len([e for e in state["snapshot"]["evaluations"] if not e["is_followup"]]) == 1


@pytest.mark.asyncio
async def test_skip_and_early_end_do_not_fabricate_unasked_scores(snapshot):
    state = await interview_graph.ainvoke({"snapshot": snapshot, "configs": {}, "command": {"type": "start"}})
    state = await interview_graph.ainvoke({"snapshot": state["snapshot"], "configs": {}, "command": {"type": "skip"}})
    state = await interview_graph.ainvoke({"snapshot": state["snapshot"], "configs": {}, "command": {"type": "end"}})
    result = await report_graph.ainvoke({"snapshot": state["snapshot"], "configs": {}})
    report = result["report"]
    assert report["overall_score"] == 0
    assert report["completion"] == {"asked": 2, "total": 5, "answered": 0, "skipped": 1}
    assert len(report["turns"]) == 1
    assert report["demo"] is True


@pytest.mark.asyncio
async def test_end_without_answers_has_no_score(snapshot):
    state = await interview_graph.ainvoke({"snapshot": snapshot, "configs": {}, "command": {"type": "end"}})
    assert report_numbers(state["snapshot"])["overall_score"] is None


@pytest.mark.asyncio
async def test_provider_failure_preserves_question_and_evaluations(snapshot, monkeypatch):
    from app.platform import graph

    state = await interview_graph.ainvoke({"snapshot": snapshot, "configs": {}, "command": {"type": "start"}})
    durable = state["snapshot"]
    durable["demo"] = False
    original = copy.deepcopy(durable)

    async def fail(*args, **kwargs):
        raise AIError("provider unavailable", unknown=True)

    monkeypatch.setattr(graph, "complete", fail)
    with pytest.raises(AIError):
        await interview_graph.ainvoke({"snapshot": durable, "configs": {"evaluate": {}},
                                       "command": {"type": "answer", "content": "这份回答必须保留"}})
    assert durable == original


@pytest.mark.asyncio
async def test_real_mode_rejects_missing_dimensions_instead_of_defaulting_scores(snapshot, monkeypatch):
    from app.platform import graph

    state = await interview_graph.ainvoke({"snapshot": snapshot, "configs": {}, "command": {"type": "start"}})
    state["snapshot"]["demo"] = False

    async def malformed(*args, **kwargs):
        return {"score": 75, "dimensions": {"professional": 75}, "feedback": "missing dimensions"}

    monkeypatch.setattr(graph, "complete", malformed)
    with pytest.raises(AIError, match="五个"):
        await interview_graph.ainvoke({"snapshot": state["snapshot"], "configs": {"evaluate": {}},
                                       "command": {"type": "answer", "content": "answer"}})


@pytest.mark.asyncio
async def test_diagnosis_does_not_generate_questions(monkeypatch):
    from app.platform import graph

    async def diagnose(config, *args, **kwargs):
        assert config["scene"] == "resume"
        return {"resume_score": 80, "suggestions": ["补充成果数据"]}

    monkeypatch.setattr(graph, "complete", diagnose)
    result = await diagnosis_graph.ainvoke({"parsed": {"skills": ["Python"]}, "configs": {"resume": {"scene": "resume"}}, "demo": False})
    assert "questions" not in result["diagnosis"]
    assert result["diagnosis"]["suggestions"] == ["补充成果数据"]


@pytest.mark.asyncio
async def test_parsing_accepts_facts_without_scores_or_suggestions(monkeypatch):
    from app.platform import graph
    from app.platform.domain.results import validate_result

    async def parse(config, prompt, **kwargs):
        assert config["scene"] == "parse"
        return validate_result("parse", {"parsed": {"skills": ["Python"]}})

    monkeypatch.setattr(graph, "complete", parse)
    result = await parsing_graph.ainvoke({"raw_text": "Python developer", "configs": {"parse": {"scene": "parse"}}, "demo": False})
    assert result["parsed"] == {"skills": ["Python"]}
    assert "diagnosis" not in result and "preparation" not in result
    assert validate_result("resume", {"resume_score": 80, "suggestions": []}) == {"resume_score": 80, "suggestions": []}


@pytest.mark.asyncio
async def test_start_generates_questions_only_for_interview(snapshot):
    snapshot["questions"] = []
    result = await interview_graph.ainvoke({"snapshot": snapshot, "configs": {}, "command": {"type": "start"}})
    assert len(result["snapshot"]["questions"]) == 5
    assert len(result["snapshot"]["messages"]) == 1
    assert snapshot["questions"] == []
