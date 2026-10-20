"""Opt-in test checkpoints. Disabled in staging/production, never exposed as an API."""

import asyncio
import json
import os
from pathlib import Path

from app.core.config import settings
from app.platform.contracts import trace_context


async def checkpoint(name):
    root = os.environ.get("PLATFORM_FAULT_DIR")
    if not root or settings.ENVIRONMENT != "development" or name != os.environ.get("PLATFORM_FAULT_POINT"):
        return
    directory = Path(root)
    directory.mkdir(parents=True, exist_ok=True)
    marker = directory / f"{os.getpid()}-{name}.json"
    marker.write_text(json.dumps({"pid": os.getpid(), "checkpoint": name, **trace_context()}), encoding="utf-8")
    # Tests wait for this durable marker, then terminate or explicitly release the process.
    async with asyncio.timeout(120):
        while not marker.with_suffix(".release").exists():
            await asyncio.sleep(.05)
