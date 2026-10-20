"""Celery 配置

配置 Celery 任务队列。
"""

from celery import Celery

from app.core.config import settings

# 创建 Celery 实例
celery_app = Celery(
    "resume_agent",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

# Celery 配置
celery_app.conf.update(
    imports=("app.platform.worker",),
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
)
