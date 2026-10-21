"""Celery 配置

配置 Celery 任务队列。
"""

import logging

from celery import Celery
from celery.schedules import crontab
from celery.signals import beat_init

from app.core.config import settings

settings.validate_runtime()
logger = logging.getLogger(__name__)
REPOSITORY_STATS_TASK = "app.tasks.repository_stats.refresh_repository_stats"

# 创建 Celery 实例
celery_app = Celery(
    "resume_agent",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

# Celery 配置
celery_app.conf.update(
    imports=("app.platform.worker", "app.tasks.repository_stats"),
    beat_schedule={
        "refresh-repository-stats-daily": {
            "task": REPOSITORY_STATS_TASK,
            "schedule": crontab(hour=3, minute=0),
            "options": {"queue": "default"},
        },
    },
    # 序列化方式
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",

    # 时区
    timezone="Asia/Shanghai",
    enable_utc=True,

    # 任务超时
    task_soft_time_limit=300,  # 5 分钟软超时
    task_time_limit=600,  # 10 分钟硬超时

    # 任务重试
    task_acks_late=True,
    task_reject_on_worker_lost=True,

    # 结果过期时间
    result_expires=3600,  # 1 小时

    # 默认队列
    task_default_queue="default",
    worker_prefetch_multiplier=1,
    broker_connection_timeout=5,
    broker_transport_options={"socket_timeout": 5, "socket_connect_timeout": 5, "max_retries": 0},
)

from app.platform.operations.worker_health import install_signals  # noqa: E402

install_signals()


@beat_init.connect(weak=False)
def initialize_repository_stats(sender, **kwargs):
    try:
        sender.app.send_task(REPOSITORY_STATS_TASK, kwargs={"only_if_missing": True},
                             queue="default", retry=False)
    except Exception:
        logger.warning("repository_stats_initialization_failed")
