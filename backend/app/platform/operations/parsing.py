"""Bounded CPU parsing; executor submissions never form an unbounded queue."""

import asyncio
import atexit
from concurrent.futures import ProcessPoolExecutor

from app.core.config import settings
from app.core.documents import extract_text
from app.platform.ai.guard import gate

_executor = None


async def parse_document(filename):
    global _executor
    from billiard import current_process

    # Celery's prefork child already occupies a bounded isolated process slot.
    # Daemonic children cannot create another multiprocessing executor.
    if current_process().daemon:
        return await asyncio.to_thread(extract_text, filename)
    if _executor is None:
        _executor = ProcessPoolExecutor(max_workers=settings.PARSER_PROCESSES)
        atexit.register(_executor.shutdown, wait=False, cancel_futures=True)
    import time

    # Hold the slot until an already-running parser actually exits on cancellation.
    async with gate("parse").enter(time.monotonic() + settings.AI_WAIT_SECONDS):
        future = asyncio.get_running_loop().run_in_executor(_executor, extract_text, filename)
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            await asyncio.shield(future)
            raise
