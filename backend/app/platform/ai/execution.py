"""Every physical request is admitted and audited separately, including tool loops."""

import asyncio
import contextlib
import json
import logging
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar

import anyio
from openai import APIConnectionError, APIStatusError, APITimeoutError

from app.core.config import settings
from app.core.documents import mask_raw_text
from app.platform.ai import flights, guard
from app.platform.contracts import PlatformError
from app.platform.domain.results import validate_result
from app.platform.operations.faults import checkpoint
from app.platform.operations.telemetry import observe_circuit, record

_deadline = ContextVar("ai_deadline", default=None)
logger = logging.getLogger(__name__)


def remaining(deadline):
    value = deadline - time.monotonic()
    if value <= 0:
        raise PlatformError("AI_TIMEOUT", "AI 总时间预算已耗尽，本次未发送", status=504)
    return value


def classify(exc, *, sent=None):
    if isinstance(exc, (APITimeoutError, TimeoutError)) or (
        isinstance(exc, PlatformError) and exc.code == "AI_TIMEOUT" and sent is not None
    ):
        unknown = sent is not False
        return PlatformError("AI_TIMEOUT",
            "模型调用超时，外部结果可能未知；请确认后手动重试" if unknown else "AI 总时间预算已耗尽，本次未发送",
            status=504, outcome="unknown" if unknown else "not_sent")
    if isinstance(exc, PlatformError):
        return exc
    if isinstance(exc, APIConnectionError):
        return PlatformError("AI_RESULT_UNKNOWN", "模型连接中断，结果未知；请确认后手动重试", outcome="unknown")
    if isinstance(exc, APIStatusError):
        if exc.status_code in {400, 401, 403, 404, 422}:
            return PlatformError("AI_CONFIGURATION_ERROR", "供应商拒绝请求，请管理员检查配置", outcome="known", retry_policy="never")
        # A 5xx does not prove that generation never ran.
        return PlatformError("AI_RESULT_UNKNOWN", "供应商未确认调用结果，请确认后手动重试", outcome="unknown")
    return PlatformError("AI_RESULT_UNKNOWN", "模型请求中断，结果可能未知，请确认后重试", outcome="unknown")


@asynccontextmanager
async def attempt(config, messages, deadline, max_tokens):
    from app.platform import runtime

    remaining(deadline)
    waiting_started = time.monotonic()
    async with guard.gate(guard.workload(config)).enter(deadline):
        breaker = guard.circuit(config)
        try:
            admission = breaker.enter()
        except PlatformError:
            await observe_circuit(config, breaker)
            await record("circuit_open", scene=config["scene"], workload=guard.workload(config))
            raise
        await observe_circuit(config, breaker)
        call_id = None
        renewal = None
        started = time.monotonic()
        state = {"usage": None, "sent": False}
        try:
            # Fail configuration before allocating quota or issuing a provider request.
            async with runtime._client(config) as client:
                budget = {**config, "_input_budget": len(json.dumps(messages, ensure_ascii=False).encode()) + 256,
                          "_output_budget": max_tokens}
                call_id = await asyncio.wait_for(runtime._begin_call(budget), remaining(deadline))
                logger.info("ai_admitted", extra={"call_id": call_id, "scene": config["scene"]})
                await record("admission", scene=config["scene"], workload=guard.workload(config), duration_ms=(time.monotonic() - waiting_started) * 1000)
                await checkpoint("before_provider_send")
                await runtime._mark_sent(call_id)
                state["sent"] = True

                async def renew():
                    while True:
                        await asyncio.sleep(max(1, settings.AI_COMMAND_LEASE_SECONDS / 3))
                        await guard.renew(runtime.async_session_maker, call_id)

                renewal = asyncio.create_task(renew())
                yield client, state
                await checkpoint("after_provider_response")
                if renewal.done():
                    renewal.result()
                await runtime._end_call(call_id, started, "completed", state["usage"])
                logger.info("ai_completed", extra={"call_id": call_id, "scene": config["scene"],
                            "duration_ms": int((time.monotonic() - started) * 1000)})
                call_id = None
            breaker.finish(False, admission)
        except BaseException as exc:
            cancelled = isinstance(exc, (asyncio.CancelledError, GeneratorExit))
            error = classify(exc, sent=state["sent"])
            if not state["sent"] and not isinstance(exc, PlatformError) and error.code != "AI_TIMEOUT":
                error = PlatformError("AI_CONFIGURATION_ERROR", "调用未发送，请检查模型配置", retry_policy="never")
            # Validation, quota, cancellation and local saturation do not affect provider health.
            attributable = state["sent"] and (error.code == "AI_TIMEOUT" or isinstance(exc, APIConnectionError) or (
                isinstance(exc, APIStatusError) and exc.status_code >= 500))
            breaker.finish(True if attributable else None, admission)
            if error.code in {"AI_OVERLOADED", "AI_QUOTA_EXCEEDED"}:
                await record("rejected", scene=config["scene"], workload=guard.workload(config))
            if error.code == "AI_TIMEOUT":
                await record("ai_timeout", scene=config["scene"], workload=guard.workload(config))
            if breaker.open_until:
                await record("circuit_open", scene=config["scene"], workload=guard.workload(config))
            if call_id:
                with anyio.CancelScope(shield=True), contextlib.suppress(Exception):
                    await runtime._end_call(call_id, started, "not_sent" if not state["sent"] else "interrupted" if cancelled else "unknown" if error.unknown else "failed",
                                            state["usage"], error.code)
            logger.warning("ai_failed", extra={"call_id": call_id, "scene": config["scene"], "code": error.code,
                           "status": error.outcome, "duration_ms": int((time.monotonic() - started) * 1000)})
            if cancelled:
                raise
            raise error from exc
        finally:
            if renewal:
                renewal.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await renewal
            await observe_circuit(config, breaker)


async def tools(calls, allowed, resume, config, deadline):
    from app.core.tools import execute_tool
    from app.platform import runtime

    results = []
    for index, call in enumerate(calls):
        name = call["name"]
        if index >= 3:
            value = "本轮工具调用预算已耗尽，请基于已有结果回答"
        elif name not in allowed:
            value = "工具未获授权"
        elif name == "verify_code_snippet":
            # Parent permit has already been returned; nested admission cannot deadlock.
            async with guard.gate("tool").enter(deadline):
                token = _deadline.set(deadline)
                try:
                    value = await asyncio.wait_for(runtime.complete({**config, "tools": []}, "分析代码，不执行代码：\n" +
                        json.dumps(call["arguments"], ensure_ascii=False), json_mode=False, _nested=True), remaining(deadline))
                finally:
                    _deadline.reset(token)
        else:
            async with guard.gate("tool").enter(deadline):
                value = await asyncio.wait_for(asyncio.to_thread(execute_tool, name, call["arguments"], resume), remaining(deadline))
        results.append({"role": "tool", "tool_call_id": call["id"], "content": str(value)[:6000]})
    return results


def assistant(calls, content=None):
    return {"role": "assistant", "content": content, "tool_calls": [
        {"id": c["id"], "type": "function", "function": {"name": c["name"],
            "arguments": json.dumps(c["arguments"], ensure_ascii=False)}} for c in calls]}


async def complete(config, prompt, *, json_mode=True, resume=None, _nested=False):
    from app.core.llm import _parse_tool_calls_from_text
    from app.core.tools import get_tools
    from app.platform import runtime

    deadline = _deadline.get() or time.monotonic() + settings.AI_TIMEOUT_SECONDS
    token = _deadline.set(deadline)
    messages = [{"role": "system", "content": config["prompt"] + ("\n只输出一个 JSON 对象。" if json_mode else "")},
                {"role": "user", "content": mask_raw_text(prompt)}]
    available = [t for t in get_tools() if t["function"]["name"] in config.get("tools", [])]

    async def invoke():
        for step in range(4):
            kwargs = {"model": config["model"], "messages": messages, "max_tokens": settings.AI_MAX_OUTPUT_TOKENS}
            if available and step < 3:
                kwargs["tools"] = available
            async def send(kwargs=kwargs):
                async with attempt(config, messages, deadline, kwargs["max_tokens"]) as (client, state):
                    response = await asyncio.wait_for(client.chat.completions.create(**kwargs), remaining(deadline))
                    state["usage"] = response.usage.model_dump() if response.usage else None
                    message = response.choices[0].message
                    content = message.content or ""
                    calls = [{"id": c.id, "name": c.function.name, "arguments": runtime.parse_json(c.function.arguments)}
                             for c in (message.tool_calls or [])] or _parse_tool_calls_from_text(content)
                    result = None
                    if not calls:
                        if not content.strip():
                            raise runtime.AIError("模型未完成回答")
                        result = runtime.parse_json(content) if json_mode else content
                        if json_mode:
                            result = validate_result(config["scene"], result)
                return {"content": content, "calls": calls, "result": result}

            envelope = await flights.run(runtime.async_session_maker, runtime._call_context.get(), config,
                {"messages": messages, "provider_step": step, "json": json_mode}, send)
            content, calls, result = envelope["content"], envelope["calls"], envelope["result"]
            if not calls:
                return result
            if step == 3:
                raise runtime.AIError("工具调用超过上限")
            messages.append(assistant(calls, content or None))
            messages.extend(await tools(calls, config.get("tools", []), resume, config, deadline))

    try:
        return await asyncio.wait_for(flights.run(runtime.async_session_maker, runtime._call_context.get(), config,
            {"prompt": prompt, "json": json_mode, "nested": _nested}, invoke), remaining(deadline))
    except TimeoutError as exc:
        raise classify(exc) from exc
    finally:
        _deadline.reset(token)


async def stream_chat(config, messages):
    from app.core.llm import _parse_tool_calls_from_text
    from app.core.tools import get_tools
    from app.platform import runtime

    deadline = time.monotonic() + settings.AI_TIMEOUT_SECONDS
    history = [{"role": "system", "content": config["prompt"]},
               *[{**m, "content": mask_raw_text(m["content"])} for m in messages]]
    available = [t for t in get_tools() if t["function"]["name"] in config.get("tools", [])]
    for step in range(4):
        kwargs = {"model": config["model"], "messages": history, "max_tokens": settings.AI_MAX_OUTPUT_TOKENS,
                  "stream": True, "stream_options": {"include_usage": True}}
        if available and step < 3:
            kwargs["tools"] = available
        async with attempt(config, history, deadline, kwargs["max_tokens"]) as (client, state):
            stream = await asyncio.wait_for(client.chat.completions.create(**kwargs), remaining(deadline))
            calls, chunks, holding = {}, [], False
            async with stream:
                iterator = stream.__aiter__()
                while True:
                    try:
                        chunk = await asyncio.wait_for(anext(iterator), remaining(deadline))
                    except StopAsyncIteration:
                        break
                    if getattr(chunk, "usage", None):
                        state["usage"] = chunk.usage.model_dump()
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    for part in delta.tool_calls or []:
                        call = calls.setdefault(part.index, {"id": "", "name": "", "arguments": ""})
                        call["id"] += part.id or ""
                        if part.function:
                            call["name"] += part.function.name or ""
                            call["arguments"] += part.function.arguments or ""
                    if delta.content:
                        chunks.append(delta.content)
                        holding = holding or "<" in delta.content
                        if not holding:
                            yield delta.content
            full = "".join(chunks)
            parsed = [{**c, "arguments": runtime.parse_json(c["arguments"])} for c in calls.values()]
            parsed = parsed or _parse_tool_calls_from_text(full)
            if not parsed and not full:
                raise runtime.AIError("模型未完成对话，请重试")
        if parsed:
            if step == 3:
                raise runtime.AIError("工具调用超过上限")
            history.append(assistant(parsed))
            token = _deadline.set(deadline)
            try:
                history.extend(await tools(parsed, config.get("tools", []), None, config, deadline))
            finally:
                _deadline.reset(token)
            continue
        if holding:
            emitted = []
            for piece in chunks:
                if "<" in piece:
                    break
                emitted.append(piece)
            yield full[len("".join(emitted)):]
        return


async def complete_vision(config, data_url):
    from app.platform import runtime

    deadline = time.monotonic() + settings.AI_TIMEOUT_SECONDS
    messages = [{"role": "system", "content": config["prompt"]}, {"role": "user", "content": [
        {"type": "text", "text": "请描述画面中可见的面试呈现并给出简短建议，不评分。"},
        {"type": "image_url", "image_url": {"url": data_url}}]}]
    async with attempt(config, messages, deadline, 400) as (client, state):
        response = await asyncio.wait_for(client.chat.completions.create(model=config["model"], max_tokens=400,
            messages=messages), remaining(deadline))
        state["usage"] = response.usage.model_dump() if response.usage else None
        content = response.choices[0].message.content
        if not content or not content.strip():
            raise runtime.AIError("视觉模型未返回可用反馈")
        return content.strip()[:2000]
