"""Transaction-scoped coordination shared by media processing and deletion."""

import hashlib

from sqlalchemy import text


async def try_media_lock(db, namespace: str, resource_id: str) -> bool:
    # PostgreSQL releases this lock on commit, rollback or connection loss.
    key = int.from_bytes(hashlib.sha256(f"{namespace}:{resource_id}".encode()).digest()[:8], "big", signed=True)
    return bool((await db.execute(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key})).scalar_one())
