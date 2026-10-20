"""Xunfei long-text TTS HTTP adapter; signed URLs never leave the backend.

Protocol: https://www.xfyun.cn/doc/tts/long_text_tts/API.html
"""

import base64
import hashlib
import hmac
from datetime import UTC, datetime
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import httpx

TTS_HOST = "api-dx.xf-yun.com"
TTS_CREATE = "/v1/private/dts_create"
TTS_QUERY = "/v1/private/dts_query"
MAX_AUDIO_BYTES = 128 * 1024 * 1024
TRUSTED_AUDIO_SUFFIXES = ("xfyun.cn", "xf-yun.com", "iflyaisol.com", "iflyrec.com")


class TTSProviderError(ValueError):
    """A definite provider rejection, safe to distinguish from unknown outcome."""


def tts_signed_url(path: str, api_key: str, api_secret: str, *, now: datetime | None = None) -> str:
    if path not in (TTS_CREATE, TTS_QUERY):
        raise ValueError("Unsupported TTS operation")
    date = format_datetime((now or datetime.now(UTC)).astimezone(UTC), usegmt=True)
    canonical = f"host: {TTS_HOST}\ndate: {date}\nPOST {path} HTTP/1.1"
    signature = base64.b64encode(hmac.new(api_secret.encode(), canonical.encode(), hashlib.sha256).digest()).decode()
    auth = f'api_key="{api_key}", algorithm="hmac-sha256", headers="host date request-line", signature="{signature}"'
    return f"https://{TTS_HOST}{path}?{urlencode({'host': TTS_HOST, 'date': date, 'authorization': base64.b64encode(auth.encode()).decode()})}"


def tts_create_body(app_id: str, text: str, voice: str, request_id: str) -> dict:
    return {
        "header": {"app_id": app_id, "request_id": request_id},
        "parameter": {"dts": {
            "vcn": voice, "language": "zh", "speed": 50, "volume": 50, "pitch": 50, "rhy": "0",
            "audio": {"encoding": "lame", "sample_rate": 16000},
            "pybuf": {"encoding": "utf8", "compress": "raw", "format": "plain"},
        }},
        "payload": {"text": {
            "encoding": "utf8", "compress": "raw", "format": "plain",
            "text": base64.b64encode(text.encode()).decode(),
        }},
    }


def parse_tts_response(payload: dict) -> dict:
    header = payload.get("header") or {}
    if header.get("code") not in (0, "0"):
        # Provider messages sometimes include request details. Expose only the code.
        raise TTSProviderError(f"语音合成服务拒绝请求（{header.get('code', 'invalid_response')}）")
    status = str(header.get("task_status", "1"))
    result = {
        "provider_task_id": header.get("task_id"),
        "status": "completed" if status == "5" else "failed" if status in ("2", "4") else "processing",
        "audio_url": None,
    }
    if status == "5":
        raw = ((payload.get("payload") or {}).get("audio") or {}).get("audio")
        if not raw:
            raise TTSProviderError("语音合成服务没有返回音频")
        try:
            audio_url = base64.b64decode(raw, validate=True).decode()
        except (ValueError, UnicodeError) as exc:
            raise TTSProviderError("语音合成音频地址无效") from exc
        validate_audio_url(audio_url)
        result["audio_url"] = audio_url
    return result


def validate_audio_url(url: str) -> None:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if (parsed.scheme not in ("http", "https") or parsed.username or parsed.password
            or parsed.port not in (None, 80, 443)
            or not any(host == suffix or host.endswith("." + suffix) for suffix in TRUSTED_AUDIO_SUFFIXES)):
        raise TTSProviderError("语音合成音频地址不在可信域名内")


async def tts_request(path: str, body: dict, api_key: str, api_secret: str) -> dict:
    # httpx has no automatic retry. A timed-out create must remain unknown.
    async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10), follow_redirects=False) as client:
        response = await client.post(tts_signed_url(path, api_key, api_secret), json=body)
        response.raise_for_status()
        return parse_tts_response(response.json())


async def download_audio(url: str, destination: Path) -> None:
    validate_audio_url(url)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".part")
    try:
        async with (
            httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10), follow_redirects=False) as client,
            client.stream("GET", url) as response,
        ):
            response.raise_for_status()
            size = 0
            with temporary.open("wb") as output:
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_AUDIO_BYTES:
                        raise TTSProviderError("语音文件超出大小限制")
                    output.write(chunk)
            if not size:
                raise TTSProviderError("语音文件为空")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
