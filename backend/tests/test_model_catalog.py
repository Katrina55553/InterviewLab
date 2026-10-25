"""Provider catalog requests are GET-only, bounded and safe to expose to the UI."""

import httpx
import pytest
from fastapi import HTTPException

from app.platform import model_catalog
from app.platform.admin import encrypt_key


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["auth", "unsupported", "overloaded", "timeout", "invalid-json", "invalid-schema"])
async def test_catalog_failure_never_exposes_upstream_secrets(monkeypatch, failure):
    def upstream(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("private-test-key", request=request)
        if failure == "invalid-json":
            return httpx.Response(200, text="private-test-key")
        if failure == "invalid-schema":
            return httpx.Response(200, json={"models": "private-test-key"})
        status = {"auth": 401, "unsupported": 404, "overloaded": 503}[failure]
        return httpx.Response(status, json={"message": "private-test-key"})

    monkeypatch.setattr(model_catalog, "model_http_client", lambda *args, **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(upstream)))
    with pytest.raises(HTTPException) as caught:
        await model_catalog.list_models("openai-api", "https://api.openai.com/v1", encrypt_key("private-test-key"))
    assert caught.value.status_code == 502
    assert "private-test-key" not in caught.value.detail and "手动填写" in caught.value.detail


@pytest.mark.asyncio
async def test_dashscope_falls_back_to_paginated_native_catalog_on_the_same_host(monkeypatch):
    calls = []

    def upstream(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer private-test-key"
        assert request.url.host == "dashscope.aliyuncs.com"
        if request.url.path == "/compatible-mode/v1/models":
            return httpx.Response(404)
        assert request.url.path == "/api/v1/models"
        assert request.url.params.get_list("capabilities") == ["TG", "Reasoning"]
        page = int(request.url.params["page_no"])
        rows = [{"model": f"text-model-{index}", "name": f"模型 {index}", "capabilities": ["TG"]}
                for index in range((page - 1) * 100, min(page * 100, 101))]
        return httpx.Response(200, json={"output": {"total": 101, "models": rows}})

    monkeypatch.setattr(model_catalog, "model_http_client", lambda *args, **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(upstream)))
    models = await model_catalog.list_models("dashscope", "https://dashscope.aliyuncs.com/compatible-mode/v1", encrypt_key("private-test-key"))
    assert len(calls) == 3 and len(models) == 101
    assert models[-1]["id"] == "text-model-100" and models[-1]["name"] == "模型 100"


def test_catalog_preserves_names_and_skips_duplicates_invalid_and_non_text_models():
    models = model_catalog.normalize_models("openai-api", [
        {"id": "account-model", "name": "账户模型", "context_window": 1024, "max_output_tokens": 8192},
        {"id": "account-model", "name": "Duplicate"}, {"id": "bad model"}, {"id": "x" * 201}, None,
        {"id": "image-only", "output_modalities": ["image"]}, {"id": "text-embedding-3-small"},
        {"id": "retired-model", "shutdown_date": "2020-01-01"}, {"id": "name-not-provided"},
    ])
    assert [(model["id"], model["name"]) for model in models] == [("account-model", "账户模型"), ("name-not-provided", "name-not-provided")]
    assert models[0]["maxTokens"] <= 1024
