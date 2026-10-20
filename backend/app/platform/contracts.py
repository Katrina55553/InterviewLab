"""Transport independent errors and server-owned correlation context."""

import uuid
from contextlib import contextmanager
from contextvars import ContextVar

_context = ContextVar("platform_trace", default=None)


def trace_context() -> dict:
    return _context.get() or {}


def trace_id() -> str:
    return trace_context().get("trace_id") or uuid.uuid4().hex


@contextmanager
def traced(*, new: bool = False, **fields):
    context = {} if new else dict(trace_context())
    context.setdefault("trace_id", uuid.uuid4().hex)
    context.update({k: v for k, v in fields.items() if v is not None})
    token = _context.set(context)
    try:
        yield context
    finally:
        _context.reset(token)


class PlatformError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 503,
                 retry_policy: str = "manual", outcome: str = "not_sent"):
        super().__init__(message)
        self.code, self.status = code, status
        self.retry_policy, self.outcome = retry_policy, outcome

    @property
    def unknown(self):
        return self.outcome == "unknown"


def error_payload(exc: Exception) -> dict:
    if isinstance(exc, PlatformError):
        return {"code": exc.code, "message": str(exc), "retry_policy": exc.retry_policy,
                "outcome": exc.outcome, "trace_id": trace_id()}
    status = getattr(exc, "status_code", 500)
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict) and "code" in detail:
        return {**detail, "trace_id": trace_id()}
    code = {400: "INVALID_REQUEST", 401: "UNAUTHENTICATED", 403: "FORBIDDEN", 404: "NOT_FOUND",
            409: "STATE_CONFLICT", 422: "INVALID_REQUEST", 429: "AI_QUOTA_EXCEEDED"}.get(status, "INTERNAL_ERROR")
    return {"code": code, "message": detail if isinstance(detail, str) else "服务暂时不可用",
            "retry_policy": "never" if status < 409 or status == 422 else "manual",
            "outcome": "not_sent" if status < 500 else "unknown", "trace_id": trace_id()}


class TraceMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        # Browser headers are deliberately not accepted as internal correlation IDs.
        with traced(new=True) as context:
            scope.setdefault("state", {})["trace_id"] = context["trace_id"]

            async def traced_send(message):
                if message["type"] == "http.response.start":
                    message["headers"] = [*message.get("headers", []),
                                          (b"x-trace-id", context["trace_id"].encode())]
                await send(message)

            await self.app(scope, receive, traced_send)
