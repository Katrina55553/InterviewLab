"""Exercise the deployed proxy trust setting at the authentication rate-limit boundary."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import yaml
from fastapi import FastAPI, HTTPException, Request
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.platform import auth

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
@pytest.mark.parametrize("compose_name", ["docker-compose.yml", "docker-compose.test.yml"])
async def test_deployed_proxy_keeps_client_auth_ip_buckets_independent(compose_name, monkeypatch):
    config = yaml.safe_load((ROOT / compose_name).read_text(encoding="utf-8"))
    trusted = config["services"]["backend"].get("environment", {}).get("FORWARDED_ALLOW_IPS", "127.0.0.1")
    counts = {}

    async def count(_script, key_count, *args):
        values = []
        for key in args[:key_count]:
            counts[key] = counts.get(key, 0) + 1
            values.append(counts[key])
        return values

    counter = SimpleNamespace(eval=AsyncMock(side_effect=count))
    monkeypatch.setattr(auth, "get_redis", lambda: counter)
    monkeypatch.setattr(auth.settings, "AUTH_IP_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(auth.settings, "AUTH_MAX_ATTEMPTS", 100)
    app = FastAPI()

    @app.post("/api/auth/login")
    async def login(request: Request):
        await auth._throttle(request, "one-account")
        raise HTTPException(401, "wrong password")

    transport = httpx.ASGITransport(app=ProxyHeadersMiddleware(app, trusted_hosts=trusted), client=("172.18.0.2", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://backend") as client:
        for address in ("198.51.100.10", "203.0.113.20"):
            for _ in range(2):
                response = await client.post("/api/auth/login", headers={"X-Forwarded-For": address})
                assert response.status_code == 401
        for address in ("198.51.100.10", "203.0.113.20"):
            response = await client.post("/api/auth/login", headers={"X-Forwarded-For": address})
            assert response.status_code == 429


def test_public_nginx_overwrites_untrusted_forwarded_client_headers():
    config = (ROOT / "frontend/nginx.conf").read_text(encoding="utf-8")
    for location in ("/api/", "/ws/"):
        block = config.split(f"location {location} {{", 1)[1].split("}", 1)[0]
        assert "proxy_set_header X-Forwarded-For $remote_addr;" in block
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    assert not compose["services"]["backend"].get("ports")
