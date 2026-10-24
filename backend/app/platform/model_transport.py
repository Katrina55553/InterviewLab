"""Restricted HTTPS egress for user-controlled OpenAI-compatible connections."""

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.core.config import settings


def normalize_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("模型服务地址无效") from exc
    if (parsed.scheme != "https" or not parsed.hostname or port not in (None, 443)
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or "\\" in value or any(ord(char) < 32 for char in value)):
        raise ValueError("模型服务必须使用不含账号、查询参数或片段的 HTTPS 地址")
    return urlunsplit(("https", parsed.hostname.lower(), parsed.path or "", "", ""))


def allowed_urls() -> list[str]:
    return [normalize_url(url) for url in settings.USER_LLM_ALLOWED_BASE_URLS.split(",") if url.strip()]


def validate_personal_url(value: str) -> str:
    normalized = normalize_url(value)
    if normalized.rstrip("/") not in {url.rstrip("/") for url in allowed_urls()}:
        raise ValueError("此模型服务地址尚未开放，请选择支持的地址或联系管理员添加")
    return normalized


def public_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return address.is_global and not address.is_multicast and not (
        isinstance(address, ipaddress.IPv6Address) and (
            address.ipv4_mapped or address.sixtofour or address.teredo
            or address not in ipaddress.ip_network("2000::/3")
        )
    )


class PublicModelTransport(httpx.AsyncBaseTransport):
    """Pin the checked IP while preserving Host and TLS SNI; never follow redirects."""

    def __init__(self, base_url: str, *, anonymous=False, response_limit=8 * 1024 * 1024):
        self.hostname = urlsplit(validate_personal_url(base_url)).hostname
        self.anonymous = anonymous
        self.response_limit = response_limit
        self.transport = httpx.AsyncHTTPTransport(retries=0)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.scheme != "https" or request.url.host != self.hostname or request.url.port not in (None, 443):
            raise ValueError("模型请求目标不符合策略")
        async with asyncio.timeout(5):
            addresses = await asyncio.get_running_loop().getaddrinfo(
                self.hostname, 443, type=socket.SOCK_STREAM,
            )
        if not addresses or any(not public_address(entry[4][0]) for entry in addresses):
            raise ValueError("模型服务必须解析为公网地址")
        headers = request.headers.copy()
        headers["Host"] = self.hostname
        if self.anonymous:
            headers.pop("Authorization", None)
        pinned = httpx.Request(
            request.method, request.url.copy_with(host=addresses[0][4][0]), headers=headers,
            stream=request.stream, extensions={**request.extensions, "sni_hostname": self.hostname},
        )
        response = await self.transport.handle_async_request(pinned)
        response.stream = BoundedStream(response.stream, self.response_limit)
        return response

    async def aclose(self):
        await self.transport.aclose()


class BoundedStream(httpx.AsyncByteStream):
    def __init__(self, stream, limit):
        self.stream, self.limit = stream, limit

    async def __aiter__(self):
        size = 0
        async for chunk in self.stream:
            size += len(chunk)
            if size > self.limit:
                await self.stream.aclose()
                raise httpx.ReadError("模型响应超过大小限制")
            yield chunk

    async def aclose(self):
        await self.stream.aclose()


def model_http_client(base_url: str, timeout: float = 15, *, anonymous=False, response_limit=8 * 1024 * 1024) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=PublicModelTransport(base_url, anonymous=anonymous, response_limit=response_limit), timeout=timeout,
                             follow_redirects=False, trust_env=False)
