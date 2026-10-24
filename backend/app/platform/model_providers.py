"""Named API Key connections use server-owned OpenAI-compatible endpoints."""

from app.platform.model_transport import allowed_urls, validate_personal_url

API_KEY_PROVIDERS = (
    {"id": "deepseek", "name": "DeepSeek", "urls": ("https://api.deepseek.com", "https://api.deepseek.com/v1"),
     "defaultModel": "deepseek-flash"},
    {"id": "openai-api", "name": "OpenAI", "urls": ("https://api.openai.com/v1",)},
    {"id": "openrouter", "name": "OpenRouter", "urls": ("https://openrouter.ai/api/v1",)},
    {"id": "siliconflow", "name": "硅基流动", "urls": ("https://api.siliconflow.cn/v1",)},
    {"id": "dashscope", "name": "阿里云百炼", "urls": ("https://dashscope.aliyuncs.com/compatible-mode/v1",)},
    {"id": "moonshot", "name": "Moonshot（Kimi）", "urls": ("https://api.moonshot.cn/v1",)},
    {"id": "bigmodel", "name": "智谱 AI", "urls": ("https://open.bigmodel.cn/api/paas/v4",)},
)


def api_key_providers() -> list[dict]:
    allowed = {url.rstrip("/") for url in allowed_urls()}
    result = []
    for provider in API_KEY_PROVIDERS:
        url = next((url for url in provider["urls"] if url in allowed), None)
        if url:
            result.append({"id": provider["id"], "name": provider["name"], "baseUrl": url,
                           "defaultModel": provider.get("defaultModel", ""),
                           "auth": {"apiKey": {"label": f'{provider["name"]} API Key', "interactive": True}}})
    return result


def provider_url(provider_id: str) -> str:
    provider = next((provider for provider in api_key_providers() if provider["id"] == provider_id), None)
    if provider is None:
        raise ValueError("请选择已开放的 API Key 服务商")
    return validate_personal_url(provider["baseUrl"])
