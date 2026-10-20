"""LLM tool-calling compatibility tests."""

from types import SimpleNamespace

from app.core import llm
from app.core.llm import (
    _parse_tool_calls_from_text,
    _strip_dsml_tool_text,
    call_llm,
    call_llm_routed,
    call_llm_stream,
    continue_with_tool_results,
)


def test_malformed_dsml_tool_call_without_arguments_is_ignored():
    content = """
简历信息，我看到候选人提到了两个具体项目，但并未列出10个产品。现在我需要针对这个存疑点进行提问。

<｜｜DSML｜｜tool_calls>
<｜｜DSML｜｜invoke name="lookup_resume_field">
<｜｜DSML｜｜invoke name="lookup_resume_field">
</｜｜DSML｜｜invoke>
</｜｜DSML｜｜tool_calls>
"""

    assert _parse_tool_calls_from_text(content) == []


def test_dsml_tool_text_is_not_displayable_content():
    content = """
简历信息，我看到候选人提到了两个具体项目，但并未列出10个产品。现在我需要针对这个存疑点进行提问。

<｜｜DSML｜｜tool_calls>
<｜｜DSML｜｜invoke name="lookup_resume_field">
</｜｜DSML｜｜tool_calls>
"""

    assert _strip_dsml_tool_text(content) == ""


def test_dsml_tool_call_with_arguments_is_parsed():
    content = """
<｜｜DSML｜｜tool_calls>
<｜｜DSML｜｜invoke name="lookup_resume_field">
{"field": "projects"}
</｜｜DSML｜｜invoke>
</｜｜DSML｜｜tool_calls>
"""

    assert _parse_tool_calls_from_text(content) == [
        {
            "id": "call_0",
            "name": "lookup_resume_field",
            "arguments": {"field": "projects"},
        },
    ]


def test_stream_drops_entire_output_when_dsml_is_split_across_chunks(monkeypatch):
    chunks = [
        "简历信息，我看到候选人提到了两个具体项目，",
        "但并未列出10个产品。",
        "<｜｜DSML｜｜tool_calls>",
        "<｜｜DSML｜｜invoke name=\"lookup_resume_field\">",
        "<｜｜DSML｜｜invoke name=\"lookup_resume_field\">",
        "<｜｜DSML｜｜invoke name=\"lookup_resume_field\">",
        "</｜｜DSML｜｜invoke>",
        "</｜｜DSML｜｜invoke>",
        "</｜｜DSML｜｜invoke>",
        "</｜｜DSML｜｜tool_calls>",
    ]

    class FakeCompletions:
        def create(self, **kwargs):
            assert kwargs["stream"] is True
            return [
                SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content=chunk))],
                )
                for chunk in chunks
            ]

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions()),
    )
    monkeypatch.setattr(llm, "_get_client", lambda: fake_client)

    assert list(call_llm_stream("system", "user")) == []


def test_stream_yields_plain_question(monkeypatch):
    chunks = ["能介绍一下", "这个项目里你的具体职责吗？"]

    class FakeCompletions:
        def create(self, **kwargs):
            assert kwargs["stream"] is True
            return [
                SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content=chunk))],
                )
                for chunk in chunks
            ]

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions()),
    )
    monkeypatch.setattr(llm, "_get_client", lambda: fake_client)

    assert list(call_llm_stream("system", "user")) == chunks


def test_plain_llm_call_drops_dsml_text(monkeypatch):
    monkeypatch.setattr(llm, "_get_client", lambda: _fake_text_client(
        "<｜｜DSML｜｜tool_calls><｜｜DSML｜｜invoke name=\"lookup_resume_field\"></｜｜DSML｜｜tool_calls>",
    ))

    assert call_llm("system", "user") == ""


def test_routed_llm_call_drops_dsml_text(monkeypatch):
    monkeypatch.setattr(llm, "_get_client", lambda: _fake_text_client(
        "我需要调用工具。<｜｜DSML｜｜tool_calls><｜｜DSML｜｜invoke name=\"lookup_resume_field\">",
    ))

    assert call_llm_routed("question", "system", "user") == ""


def test_continue_with_tool_results_drops_dsml_text(monkeypatch):
    monkeypatch.setattr(llm, "_get_client", lambda: _fake_text_client(
        "<｜｜DSML｜｜tool_calls><｜｜DSML｜｜invoke name=\"lookup_resume_field\">",
    ))

    assert continue_with_tool_results("system", [{"role": "user", "content": "user"}], []) == ""


def _fake_text_client(content: str):
    class FakeCompletions:
        def create(self, **kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=content, tool_calls=None),
                    ),
                ],
                usage=None,
            )

    return SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions()),
    )
