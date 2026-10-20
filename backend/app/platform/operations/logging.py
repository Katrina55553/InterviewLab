"""Allowlisted structured logging: never serialize exception values or request data."""

import json
import logging
import traceback

from app.platform.contracts import trace_context

# Only fixed event names may reach the sink, including from legacy app loggers.
# Checking logger names or %-style arguments cannot detect f-string payloads.
EVENTS = frozenset({
    "request_failed", "redis_connected", "redis_degraded", "ai_admitted", "ai_completed",
    "ai_failed", "job_failed", "job_received", "job_finished", "job_published",
    "outbox_delivery_unknown", "dispatch_failed", "inspection_failed",
})


class SafeJSONFormatter(logging.Formatter):
    def format(self, record):
        # Third party messages may contain URLs, SQL parameters, or credentials.
        data = {"level": record.levelname, "logger": record.name,
                "event": record.msg if isinstance(record.msg, str) and record.msg in EVENTS and not record.args else "log",
                **{k: v for k, v in trace_context().items()
                   if k in {"trace_id", "request_id", "command_id", "job_id", "flight_id", "call_id", "connection_id"}}}
        for field in ("call_id", "scene", "status", "code", "duration_ms", "delivery_id"):
            if hasattr(record, field):
                data[field] = getattr(record, field)
        if record.exc_info:
            data["exception_type"] = record.exc_info[0].__name__
            data["stack"] = [{"file": f.name, "line": f.lineno} for f in traceback.extract_tb(record.exc_info[2])]
        return json.dumps(data, ensure_ascii=False)


def configure_logging():
    handler = logging.StreamHandler()
    handler.setFormatter(SafeJSONFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "celery", "celery.task", "kombu", "httpx", "httpcore"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
