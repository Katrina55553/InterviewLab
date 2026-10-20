"""Gateway contracts with deterministic SDK streams; no real provider acceptance claim."""

import asyncio
import copy
import json
from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.platform import runtime
from app.platform.models import Agent, Provider


def usage(prompt, completion):
    data = {"prompt_tokens": prompt, "completion_tokens": completion}
    return SimpleNamespace(**data, model_dump=lambda: dict(data))


def chunk(content=None, *, calls=None, token_usage=None):
    choices = [] if token_usage is not None else [SimpleNamespace(delta=SimpleNamespace(content=content, tool_calls=calls))]
    return SimpleNamespace(choices=choices, usage=token_usage)


def tool_delta(index, *, name="lookup_resume_field", arguments=None, call_id=None):
    return SimpleNamespace(index=index, id=call_id or f"call-{index}", function=SimpleNamespace(
        name=name, arguments=json.dumps(arguments or {"field": "skills"}),
    ))


def response(content, *, calls=None, token_usage=None):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=calls))],
                           usage=token_usage)


class SDKStream:
    def __init__(self, chunks, *, gate=None):
        self.chunks = chunks
        self.gate = gate
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for index, item in enumerate(self.chunks):
            if self.gate is not None and index == 1:
                await self.gate.wait()
            yield item


class SDK:
    """A request log with scripted streaming and ordinary SDK responses."""

    def __init__(self, responses):
        self.responses = deque(responses)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **kwargs):
        self.requests.append(copy.deepcopy(kwargs))
        if not self.responses:
            raise AssertionError("Unexpected provider call")
        return self.responses.popleft()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


@pytest.fixture
def audit(monkeypatch):
    begin = AsyncMock(return_value="audit-call")
    end = AsyncMock()
    monkeypatch.setattr(runtime, "_begin_call", begin)
    monkeypatch.setattr(runtime, "_end_call", end)
    monkeypatch.setattr(runtime, "_mark_sent", AsyncMock())
    monkeypatch.setattr(runtime.settings, "AI_MAX_CONCURRENCY", 1)
    return begin, end


def configuration(*, tools=None, scene="chat"):
    return {"scene": scene, "prompt": "system instructions", "model": "fake-model", "enabled": True,
            "tools": [] if tools is None else tools}


def audit_usage(end):
    args, kwargs = end.await_args
    return args[3] if len(args) > 3 else kwargs.get("usage")


@pytest.mark.asyncio
async def test_first_chunk_arrives_before_provider_completion(monkeypatch, audit):
    gate = asyncio.Event()
    provider_stream = SDKStream([chunk("first"), chunk("second"), chunk(token_usage=usage(12, 4))], gate=gate)
    sdk = SDK([provider_stream])
    monkeypatch.setattr(runtime, "_client", lambda config: sdk)
    source = runtime.stream_chat(configuration(), [{"role": "user", "content": "hello"}])
    try:
        assert await asyncio.wait_for(anext(source), timeout=1) == "first"
        assert provider_stream.closed is False
        audit[1].assert_not_awaited()
        gate.set()
        assert [piece async for piece in source] == ["second"]
    finally:
        await source.aclose()
    assert provider_stream.closed is True
    assert audit[1].await_args.args[2] == "completed"
    assert audit_usage(audit[1]) == {"prompt_tokens": 12, "completion_tokens": 4}
    assert sdk.requests[0]["stream_options"] == {"include_usage": True}


@pytest.mark.asyncio
async def test_all_declared_tool_ids_receive_results_even_when_execution_budget_is_three(monkeypatch, audit):
    from app.core import tools

    execute = Mock(return_value="lookup result")
    monkeypatch.setattr(tools, "execute_tool", execute)
    sdk = SDK([
        SDKStream([chunk(calls=[tool_delta(index) for index in range(5)]), chunk(token_usage=usage(11, 4))]),
        SDKStream([chunk("final answer"), chunk(token_usage=usage(17, 6))]),
    ])
    monkeypatch.setattr(runtime, "_client", lambda config: sdk)
    result = [piece async for piece in runtime.stream_chat(configuration(tools=["lookup_resume_field"]), [])]
    assert result == ["final answer"]
    second_request = sdk.requests[1]["messages"]
    declared = [call["id"] for message in second_request for call in message.get("tool_calls", [])]
    answered = [message["tool_call_id"] for message in second_request if message["role"] == "tool"]
    assert sorted(declared) == sorted(answered) == [f"call-{index}" for index in range(5)]
    assert execute.call_count == 3
    assert audit[0].await_count == audit[1].await_count == 2
    assert sum(c.args[3]["prompt_tokens"] for c in audit[1].await_args_list) == 28
    assert sum(c.args[3]["completion_tokens"] for c in audit[1].await_args_list) == 10


@pytest.mark.asyncio
async def test_stream_assembles_tool_argument_fragments_before_execution(monkeypatch, audit):
    from app.core import tools

    execute = Mock(return_value="skill details")
    monkeypatch.setattr(tools, "execute_tool", execute)
    first = SimpleNamespace(index=0, id="fragmented", function=SimpleNamespace(name="lookup_", arguments='{"field":'))
    second = SimpleNamespace(index=0, id=None, function=SimpleNamespace(name="resume_field", arguments='"skills"}'))
    sdk = SDK([SDKStream([chunk(calls=[first]), chunk(calls=[second])]), SDKStream([chunk("answer")])])
    monkeypatch.setattr(runtime, "_client", lambda config: sdk)
    assert [piece async for piece in runtime.stream_chat(configuration(tools=["lookup_resume_field"]), [])] == ["answer"]
    execute.assert_called_once_with("lookup_resume_field", {"field": "skills"}, None)


@pytest.mark.asyncio
async def test_nested_code_verification_with_concurrency_one_does_not_deadlock(monkeypatch, audit):
    verify = SimpleNamespace(id="verify", function=SimpleNamespace(name="verify_code_snippet", arguments='{"code":"print(1)"}'))
    sdk = SDK([
        response(None, calls=[verify], token_usage=usage(10, 2)),
        response("代码仅作分析，未执行", token_usage=usage(20, 8)),
        response('{"result":"ok"}', token_usage=usage(5, 3)),
    ])
    monkeypatch.setattr(runtime, "_client", lambda config: sdk)
    result = await asyncio.wait_for(runtime.complete(configuration(tools=["verify_code_snippet"]), "verify this code"), timeout=2)
    assert result == {"result": "ok"}
    assert len(sdk.requests) == 3
    assert not sdk.requests[1].get("tools")
    assert audit[0].await_count == audit[1].await_count == 3
    usages = [call.args[3] if len(call.args) > 3 else call.kwargs.get("usage") for call in audit[1].await_args_list]
    assert {"prompt_tokens": 20, "completion_tokens": 8} in usages
    assert {"prompt_tokens": 10, "completion_tokens": 2} in usages
    assert {"prompt_tokens": 5, "completion_tokens": 3} in usages


@pytest.mark.asyncio
async def test_unapproved_tool_never_executes_but_receives_protocol_result(monkeypatch, audit):
    from app.core import tools

    execute = Mock()
    monkeypatch.setattr(tools, "execute_tool", execute)
    import time

    from app.platform.ai.execution import tools as run_tools

    results = await run_tools([{"id": "denied", "name": "delete_files", "arguments": {}}],
                                   ["lookup_resume_field"], None, configuration(), time.monotonic() + 5)
    assert results == [{"role": "tool", "tool_call_id": "denied", "content": "工具未获授权"}]
    execute.assert_not_called()


@pytest.mark.asyncio
async def test_cancelled_stream_closes_provider_and_records_interruption(monkeypatch, audit):
    gate = asyncio.Event()
    sdk_stream = SDKStream([chunk("partial"), chunk("unreachable")], gate=gate)
    sdk = SDK([sdk_stream])
    monkeypatch.setattr(runtime, "_client", lambda config: sdk)
    source = runtime.stream_chat(configuration(), [])
    assert await anext(source) == "partial"
    reader = asyncio.create_task(anext(source))
    await asyncio.sleep(0)
    reader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await reader
    assert sdk_stream.closed is True
    assert audit[1].await_args.args[2] == "interrupted"


@pytest.mark.asyncio
async def test_cancelled_completion_records_interruption_without_retry(monkeypatch, audit):
    entered = asyncio.Event()
    sdk = SDK([])
    calls = []

    async def slow_response(**kwargs):
        calls.append(kwargs)
        entered.set()
        await asyncio.Event().wait()

    sdk.chat.completions.create = slow_response
    monkeypatch.setattr(runtime, "_client", lambda config: sdk)
    pending = asyncio.create_task(runtime.complete(configuration(), "answer"))
    await asyncio.wait_for(entered.wait(), timeout=2)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert len(calls) == 1
    assert audit[1].await_args.args[2] == "interrupted"


class ConfigSession:
    def __init__(self, agents, providers):
        self.rows = deque([agents, providers])

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def scalars(self, statement):
        rows = self.rows.popleft()
        return SimpleNamespace(all=lambda: rows)


def provider(identifier, *, vision=False, enabled=True):
    return Provider(id=identifier, name=identifier, base_url="https://provider.invalid/v1", model="model-v1",
                    encrypted_key="encrypted-test-key", vision=vision, enabled=enabled, version=2)


@pytest.mark.asyncio
async def test_retired_tools_in_stored_agents_are_filtered_without_mutating_rows(monkeypatch):
    from app.platform.admin import agent_json

    agent = Agent(id="old-agent", name="Old agent", scene="chat", enabled=True, version=1,
                  tools=["retired_tool", "lookup_resume_field"])
    monkeypatch.setattr(runtime, "async_session_maker", lambda: ConfigSession([agent], []))
    config = (await runtime.freeze_configs())["chat"]
    assert config["tools"] == ["lookup_resume_field"]
    assert agent_json(agent)["tools"] == ["lookup_resume_field"]
    assert agent.tools == ["retired_tool", "lookup_resume_field"]


@pytest.mark.asyncio
async def test_frozen_vision_selects_enabled_image_provider_and_copies_version(monkeypatch):
    selected = provider("enabled-vision", vision=True)
    providers = [provider("text-model"), provider("disabled-vision", vision=True, enabled=False), selected]
    monkeypatch.setattr(runtime, "async_session_maker", lambda: ConfigSession([], providers))
    monkeypatch.setattr(runtime.settings, "LLM_API_KEY", "")
    configs = await runtime.freeze_configs()
    assert configs["vision"]["provider_id"] == selected.id
    assert configs["vision"]["enabled"] is True
    assert configs["vision"]["provider_version"] == 2
    assert configs["vision"]["encrypted_key"] == "encrypted-test-key"
    selected.model, selected.version = "changed-after-freeze", 3
    assert configs["vision"]["model"] == "model-v1"
    assert configs["vision"]["provider_version"] == 2


@pytest.mark.asyncio
async def test_disabled_vision_agent_prevents_fallback_to_another_provider(monkeypatch):
    providers = [provider("bound-vision", vision=True), provider("another-vision", vision=True)]
    disabled = Agent(id="vision-agent", name="disabled", scene="vision", enabled=False,
                     provider_id="bound-vision", prompt="frozen prompt", tools=[], version=4)
    monkeypatch.setattr(runtime, "async_session_maker", lambda: ConfigSession([disabled], providers))
    monkeypatch.setattr(runtime.settings, "LLM_API_KEY", "")
    config = (await runtime.freeze_configs())["vision"]
    assert config["enabled"] is False
    assert config["provider_id"] == "bound-vision"
    assert config["agent_version"] == 4
    assert config["prompt"] == "frozen prompt"


@pytest.mark.asyncio
async def test_missing_vision_provider_does_not_fall_back_to_text_environment(monkeypatch):
    monkeypatch.setattr(runtime, "async_session_maker", lambda: ConfigSession([], [provider("text-only")]))
    monkeypatch.setattr(runtime.settings, "LLM_API_KEY", "test-environment-key")
    config = (await runtime.freeze_configs())["vision"]
    assert config["enabled"] is False
    assert config["provider_id"] is None


@pytest.mark.asyncio
async def test_explicit_vision_agent_cannot_enable_a_text_only_provider(monkeypatch):
    text_provider = provider("text-only", vision=False)
    agent = Agent(id="vision-agent", name="vision", scene="vision", enabled=True,
                  provider_id=text_provider.id, prompt="image feedback", tools=[], version=1)
    monkeypatch.setattr(runtime, "async_session_maker", lambda: ConfigSession([agent], [text_provider]))
    monkeypatch.setattr(runtime.settings, "LLM_API_KEY", "")
    config = (await runtime.freeze_configs())["vision"]
    assert config["enabled"] is False


@pytest.mark.asyncio
async def test_vision_gateway_shares_audit_usage_and_single_provider_request(monkeypatch, audit):
    sdk = SDK([response("镜头高度合适。", token_usage=usage(30, 10))])
    monkeypatch.setattr(runtime, "_client", lambda config: sdk)
    with runtime.call_scope("owner", "interview"):
        result = await runtime.complete_vision(configuration(scene="vision"), "data:image/jpeg;base64,/9j/")
    assert result == "镜头高度合适。"
    assert len(sdk.requests) == 1
    assert audit_usage(audit[1]) == {"prompt_tokens": 30, "completion_tokens": 10}
    content = sdk.requests[0]["messages"][1]["content"]
    assert content[1]["image_url"]["url"] == "data:image/jpeg;base64,/9j/"
