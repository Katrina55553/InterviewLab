"""Container-only test entry points using real HTTP against the protocol fixture.

Production entry points never import or install this adapter. The exact database,
broker, secret and endpoint allowlist must match docker-compose.test.yml.
"""

import argparse

import httpx

from app.core.config import settings
from app.platform import model_transport

FIXTURE_BASE_URL = "https://fixture.invalid/v1"
FIXTURE_API_KEY = "test-fixture-only"
TEST_DATABASE_URL = "postgresql+asyncpg://postgres:test-only@postgres:5432/resume_agent_test"
TEST_SECRET_KEY = "isolated-engineering-test-secret-key-2026"


def assert_isolated_environment() -> None:
    expected = {
        "DATABASE_URL": TEST_DATABASE_URL,
        "REDIS_URL": "redis://redis:6379/0",
        "CELERY_BROKER_URL": "redis://redis:6379/1",
        "CELERY_RESULT_BACKEND": "redis://redis:6379/2",
        "SECRET_KEY": TEST_SECRET_KEY,
        "ENVIRONMENT": "development",
        "USER_LLM_ALLOWED_BASE_URLS": FIXTURE_BASE_URL,
    }
    if any(getattr(settings, name) != value for name, value in expected.items()):
        raise RuntimeError("Engineering fixture requires the isolated test Compose environment")


def fixture_selection() -> dict:
    return {
        "connectionType": "custom", "authMethod": "api-key", "providerId": "custom:" + FIXTURE_BASE_URL,
        "modelId": "test", "custom": {
            "baseUrl": FIXTURE_BASE_URL, "api": "openai-completions", "displayName": "Isolated protocol fixture",
            "model": {"input": ["text"], "reasoning": False, "contextWindow": 32768, "maxTokens": 4096},
        },
    }


class FixtureTransport(httpx.AsyncBaseTransport):
    def __init__(self, *, anonymous=False, response_limit=8 * 1024 * 1024):
        self.anonymous, self.response_limit = anonymous, response_limit
        self.transport = httpx.AsyncHTTPTransport(retries=0)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if (request.url.scheme != "https" or request.url.host != "fixture.invalid"
                or request.url.port not in (None, 443) or not request.url.path.startswith("/v1/")
                or request.url.username or request.url.password or request.url.fragment):
            raise ValueError("Engineering model transport accepts only the isolated fixture endpoint")
        headers = request.headers.copy()
        if self.anonymous:
            headers.pop("Authorization", None)
        elif headers.get("Authorization") != "Bearer " + FIXTURE_API_KEY:
            raise ValueError("Engineering model transport accepts only the test fixture credential")
        headers["Host"] = "provider:8000"
        forwarded = httpx.Request(
            request.method, request.url.copy_with(scheme="http", host="provider", port=8000), headers=headers,
            stream=request.stream, extensions=request.extensions,
        )
        response = await self.transport.handle_async_request(forwarded)
        response.stream = model_transport.BoundedStream(response.stream, self.response_limit)
        return response

    async def aclose(self):
        await self.transport.aclose()


def fixture_http_client(base_url: str, timeout: float = 15, *, anonymous=False,
                        response_limit=8 * 1024 * 1024) -> httpx.AsyncClient:
    assert_isolated_environment()
    if base_url.rstrip("/") != FIXTURE_BASE_URL:
        raise ValueError("Engineering model transport accepts only the isolated fixture endpoint")
    return httpx.AsyncClient(transport=FixtureTransport(anonymous=anonymous, response_limit=response_limit),
                             timeout=timeout, follow_redirects=False, trust_env=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=["web", "worker"])
    args = parser.parse_args()
    assert_isolated_environment()
    model_transport.model_http_client = fixture_http_client
    if args.role == "web":
        import uvicorn

        uvicorn.run("app.main:app", host="0.0.0.0", port=8000)
    else:
        from app.tasks.celery_app import celery_app

        celery_app.worker_main(["worker", "--loglevel=warning", "--concurrency=2", "-Q", "prepare,report,default"])


if __name__ == "__main__":
    main()
