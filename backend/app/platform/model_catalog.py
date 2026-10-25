"""Read an account's model catalog without inference or persistent catalog caches."""

import asyncio
from datetime import date
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import HTTPException

from app.core.config import settings
from app.platform.admin import decrypt_key
from app.platform.model_transport import model_http_client

MAX_MODELS = 5000


def positive(value, fallback):
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 < value <= 10000000 else fallback


def normalize_models(provider_id: str, rows: list) -> list[dict]:
    result, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        model_id = row.get("id") or row.get("model")
        if not isinstance(model_id, str) or not 0 < len(model_id) <= 200 or any(char.isspace() or ord(char) < 32 for char in model_id):
            continue
        if model_id in seen:
            continue
        architecture = row.get("architecture") if isinstance(row.get("architecture"), dict) else {}
        output = row.get("output_modalities", architecture.get("output_modalities"))
        if isinstance(output, list) and "text" not in output:
            continue
        capabilities = row.get("capabilities")
        if provider_id == "dashscope" and isinstance(capabilities, list) and not {"TG", "Reasoning"}.intersection(capabilities):
            continue
        if provider_id == "openai-api" and (model_id.startswith(("text-embedding-", "tts-", "whisper-", "dall-e-", "gpt-image-", "omni-moderation-", "text-moderation-"))
                or any(part in model_id for part in ("realtime", "transcribe", "-tts", "-audio"))):
            continue
        shutdown = row.get("shutdown_date") or row.get("expiration_date")
        if isinstance(shutdown, str):
            try:
                if date.fromisoformat(shutdown[:10]) <= date.today():
                    continue
            except ValueError:
                pass
        name = row.get("name") or row.get("display_name") or model_id
        if not isinstance(name, str) or not name.strip() or len(name) > 200 or any(ord(char) < 32 for char in name):
            name = model_id
        context = positive(row.get("context_window") or row.get("context_length"), 8192)
        top = row.get("top_provider") if isinstance(row.get("top_provider"), dict) else {}
        maximum = positive(row.get("max_output_tokens") or top.get("max_completion_tokens"), 8192)
        result.append({"id": model_id, "providerId": provider_id, "name": name.strip(),
                       "description": "来自服务商模型列表；实际可用性以连接测试为准。", "input": ["text"],
                       "reasoning": bool(row.get("effort") or row.get("reasoning")), "contextWindow": context,
                       "maxTokens": min(settings.AI_MAX_OUTPUT_TOKENS, context, maximum)})
        seen.add(model_id)
    return result


def model_rows(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list) or len(payload["data"]) > MAX_MODELS:
        raise ValueError("Invalid model catalog")
    return payload["data"]


async def dashscope_rows(client, base_url, headers):
    parsed = urlsplit(base_url)
    url = urlunsplit((parsed.scheme, parsed.netloc, "/api/v1/models", "", ""))
    rows = []
    for page in range(1, MAX_MODELS // 100 + 1):
        response = await client.get(url, headers=headers, params=[("page_no", str(page)), ("page_size", "100"),
                                                               ("capabilities", "TG"), ("capabilities", "Reasoning")])
        response.raise_for_status()
        payload = response.json()
        output = payload.get("output") if isinstance(payload, dict) else None
        if not isinstance(output, dict) or not isinstance(output.get("models"), list):
            raise ValueError("Invalid model catalog")
        batch = output["models"]
        rows.extend(batch)
        if len(rows) > MAX_MODELS:
            raise ValueError("Model catalog too large")
        total = output.get("total")
        if not batch or (isinstance(total, int) and len(rows) >= total) or (not isinstance(total, int) and len(batch) < 100):
            return rows
    raise ValueError("Model catalog too large")


async def list_models(provider_id: str, base_url: str, encrypted_key: str) -> list[dict]:
    try:
        headers = {"Authorization": "Bearer " + decrypt_key(encrypted_key), "Accept": "application/json"}
        path = "/models/user" if provider_id == "openrouter" else "/models"
        params = {"sub_type": "chat"} if provider_id == "siliconflow" else None
        async with asyncio.timeout(12):
            async with model_http_client(base_url, timeout=8, response_limit=4 * 1024 * 1024) as client:
                response = await client.get(base_url.rstrip("/") + path, headers=headers, params=params)
                if provider_id == "dashscope" and response.status_code in {404, 405}:
                    rows = await dashscope_rows(client, base_url, headers)
                else:
                    response.raise_for_status()
                    rows = model_rows(response.json())
        return normalize_models(provider_id, rows)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in {401, 403}:
            message = "服务商拒绝读取模型列表，请检查 API Key 或模型读取权限。可手动填写模型名称。"
        elif exc.response.status_code in {404, 405}:
            message = "此服务商未提供兼容的模型列表，请手动填写模型名称。"
        else:
            message = "模型列表读取失败，请稍后重试或手动填写模型名称。"
        raise HTTPException(502, message) from None
    except (httpx.HTTPError, TimeoutError, ValueError, TypeError):
        raise HTTPException(502, "模型列表读取失败，请稍后重试或手动填写模型名称。") from None
