"""Adapt the ChatGPT-plan Responses stream to the platform's completion boundary."""

import time
from types import SimpleNamespace

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion, ChatCompletionChunk

from app.core.config import settings
from app.platform.contracts import PlatformError
from app.platform.subscriptions import BASE_URL, access_token, oauth_http_client


def request_body(messages, tools=None):
    instructions, items = [], []
    for message in messages:
        role, content = message["role"], message.get("content")
        if role == "system":
            instructions.append(content or "")
        elif role == "tool":
            items.append({"type": "function_call_output", "call_id": message["tool_call_id"], "output": content or ""})
        else:
            if content:
                if isinstance(content, list):
                    content = [{"type": "input_text", "text": part["text"]} if part["type"] == "text" else
                               {"type": "input_image", "image_url": part["image_url"]["url"]} for part in content]
                items.append({"role": role, "content": content})
            for call in message.get("tool_calls") or []:
                items.append({"type": "function_call", "call_id": call["id"], "name": call["function"]["name"],
                              "arguments": call["function"]["arguments"], "namespace": "interview"})
    result = {"input": items, "instructions": "\n".join(instructions), "store": False, "stream": True}
    if tools:
        result["tools"] = [{"type": "namespace", "name": "interview", "description": "面试练习工具",
            "tools": [{"type": "function", **tool["function"], "strict": False} for tool in tools]}]
    return result


def usage(value):
    if not value:
        return None
    return {"prompt_tokens": value.get("input_tokens", 0), "completion_tokens": value.get("output_tokens", 0),
            "total_tokens": value.get("total_tokens", value.get("input_tokens", 0) + value.get("output_tokens", 0))}


def incomplete(code="AI_RESULT_UNKNOWN"):
    return PlatformError(code, "订阅模型未完成响应，请检查计划额度或重新授权后手动重试", outcome="unknown")


class SubscriptionStream:
    def __init__(self, source, model, response_limit):
        self.source, self.model, self.limit = source, model, response_limit
        self.completed, self.bytes = False, 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.source.close()

    def __aiter__(self):
        return self.chunks()

    async def chunks(self):
        tool_index = 0
        async for event in self.source:
            value = event.model_dump()
            kind = value.get("type")
            delta, consumed = {}, None
            if kind == "response.output_text.delta":
                text = value.get("delta", "")
                self.bytes += len(text.encode())
                if self.bytes > self.limit:
                    raise incomplete()
                delta = {"content": text}
            elif kind == "response.output_item.done" and value.get("item", {}).get("type") == "function_call":
                item = value["item"]
                arguments = item.get("arguments", "")
                self.bytes += len(arguments.encode())
                if self.bytes > self.limit:
                    raise incomplete()
                delta = {"tool_calls": [{"index": tool_index, "id": item["call_id"], "type": "function",
                    "function": {"name": item["name"], "arguments": arguments}}]}
                tool_index += 1
            elif kind == "response.completed":
                self.completed = True
                consumed = usage(value.get("response", {}).get("usage"))
            elif kind in {"response.failed", "response.incomplete", "error"}:
                error = value.get("response", {}).get("error") or value.get("error") or {}
                code = "AI_QUOTA_EXCEEDED" if error.get("code") in {
                    "subscription_sharing_usage_limit_exceeded", "subscription_sharing_usage_unavailable", "rate_limit_exceeded",
                } else "AI_RESULT_UNKNOWN"
                raise incomplete(code)
            if delta or consumed:
                yield ChatCompletionChunk.model_validate({"id": "subscription-response", "object": "chat.completion.chunk",
                    "created": int(time.time()), "model": self.model, "usage": consumed,
                    "choices": [{"index": 0, "delta": delta, "finish_reason": None}] if delta else []})
        if not self.completed:
            raise incomplete()


class SubscriptionClient:
    def __init__(self, config):
        self.config = config
        self.client = None
        self.chat = SimpleNamespace(completions=self)

    async def __aenter__(self):
        from app.platform import runtime

        if self.config.get("base_url") != BASE_URL or not self.config.get("enabled", True):
            raise PlatformError("AI_CONFIGURATION_ERROR", "订阅模型配置无效或已停用", retry_policy="never")
        token = await access_token(self.config, runtime.async_session_maker)
        self.client = AsyncOpenAI(api_key=token, base_url=BASE_URL, max_retries=0,
                                  timeout=settings.AI_TIMEOUT_SECONDS, http_client=oauth_http_client())
        return self

    async def __aexit__(self, *_):
        if self.client:
            await self.client.close()

    async def create(self, *, model, messages, stream=False, tools=None, **_):
        source = await self.client.responses.create(model=model, **request_body(messages, tools))
        adapted = SubscriptionStream(source, model, self.config.get("response_limit", 8 * 1024 * 1024))
        if stream:
            return adapted
        text, calls, consumed = [], [], None
        async with adapted:
            async for chunk in adapted:
                if chunk.usage:
                    consumed = chunk.usage.model_dump()
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.content:
                    text.append(delta.content)
                for call in delta.tool_calls or []:
                    calls.append({"id": call.id, "type": "function", "function": {
                        "name": call.function.name, "arguments": call.function.arguments}})
        return ChatCompletion.model_validate({"id": "subscription-response", "object": "chat.completion",
            "created": int(time.time()), "model": model, "usage": consumed, "choices": [{"index": 0,
                "finish_reason": "tool_calls" if calls else "stop", "message": {
                    "role": "assistant", "content": "".join(text), "tool_calls": calls or None}}]})


async def list_models(config, sessions):
    token = await access_token(config, sessions)
    async with oauth_http_client() as client:
        response = await client.get(BASE_URL + "/models", headers={"Authorization": "Bearer " + token})
        response.raise_for_status()
        catalog = response.json()
    values = catalog.get("models", [])
    return [{"id": model["slug"], "providerId": "openai", "name": model.get("display_name") or model["slug"],
             "description": "来自当前 ChatGPT 账户；实际可用性以连接测试为准。", "input": ["text"],
             "reasoning": bool(model.get("reasoning", False)), "contextWindow": 8192,
             "maxTokens": min(settings.AI_MAX_OUTPUT_TOKENS, 8192)}
            for model in values if model.get("visibility") == "list" and isinstance(model.get("slug"), str)]
