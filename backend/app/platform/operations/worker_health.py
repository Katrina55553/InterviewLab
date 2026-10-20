"""Celery parent heartbeat uses a separate connection, never a child's async loop."""

import socket

from celery.signals import heartbeat_sent, setup_logging, task_postrun, worker_process_init, worker_ready


def write_heartbeat(**kwargs):
    import psycopg2

    from app.core.config import settings
    try:
        url = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
        with psycopg2.connect(url, connect_timeout=3) as db, db.cursor() as cursor:
            cursor.execute("""INSERT INTO platform_instance_heartbeats(id, role, last_seen, last_progress, ready)
                VALUES (%s, 'worker', timezone('UTC', clock_timestamp()), timezone('UTC', clock_timestamp()), true)
                ON CONFLICT (id) DO UPDATE SET last_seen=EXCLUDED.last_seen,
                last_progress=EXCLUDED.last_progress, ready=true""", ("worker:" + socket.gethostname(),))
    except Exception:
        pass


def child_init(**kwargs):
    from app.core.database import engine
    engine.sync_engine.dispose(close=False)


def install_signals():
    def logging_setup(**kwargs):
        from app.platform.operations.logging import configure_logging
        configure_logging()

    setup_logging.connect(logging_setup, weak=False)
    worker_process_init.connect(child_init, weak=False)
    worker_ready.connect(write_heartbeat, weak=False)
    heartbeat_sent.connect(write_heartbeat, weak=False)
    task_postrun.connect(write_heartbeat, weak=False)
