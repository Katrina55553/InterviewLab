"""应用配置模块

使用 pydantic-settings 从环境变量读取配置。
支持 .env 文件和系统环境变量。
"""

import re

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置类"""

    # 数据库配置
    DATABASE_URL: str = "postgresql+asyncpg://user:password@localhost:5432/resume_agent"

    # Redis 配置
    REDIS_URL: str = "redis://localhost:6379/0"

    # LLM API（OpenAI 兼容格式，支持 DeepSeek 等第三方）
    LLM_BASE_URL: str = "https://api.deepseek.com"
    LLM_API_KEY: str = ""
    LLM_MODEL: str = "deepseek-chat"
    LLM_MODEL_LIGHT: str = "deepseek-chat"
    LLM_MODEL_HEAVY: str = "deepseek-reasoner"

    # 安全配置
    SECRET_KEY: str = Field(default="change-me-in-production", min_length=16)

    # 新平台：默认调用真实服务；演示降级需要显式打开。
    PLATFORM_DEMO_MODE: bool = False
    AUTH_SESSION_HOURS: int = Field(default=168, ge=1, le=720)
    AUTH_COOKIE_SECURE: bool = False
    AUTH_RATE_LIMIT_WINDOW_SECONDS: int = Field(default=900, ge=60, le=86400)
    AUTH_MAX_ATTEMPTS: int = Field(default=10, ge=1, le=100)
    AUTH_IP_MAX_ATTEMPTS: int = Field(default=60, ge=1, le=1000)
    AI_TIMEOUT_SECONDS: int = Field(default=90, ge=1, le=600)
    AI_MAX_CONCURRENCY: int = Field(default=4, ge=1, le=64)
    AI_COMMAND_LEASE_SECONDS: int = Field(default=150, ge=10, le=1800)

    # 讯飞媒体能力；空凭据会明确禁用对应能力。
    XFYUN_APP_ID: str = ""
    XFYUN_API_KEY: str = ""
    XFYUN_API_SECRET: str = ""
    XFYUN_ASR_URL: str = "wss://office-api-ast-dx.iflyaisol.com/ast/communicate/v1"
    XFYUN_TTS_VOICE: str = "x4_mingge"
    MEDIA_STORAGE_DIR: str = "uploads/media"

    # CORS 配置
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:5173"

    # Celery 配置
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    # 文件上传配置
    MAX_UPLOAD_SIZE_MB: int = Field(default=10, ge=1, le=100)
    ALLOWED_EXTENSIONS: str = "pdf,docx,doc,txt"

    # 限流配置
    RATE_LIMIT_PER_MINUTE: int = Field(default=60, ge=1, le=1000)
    TOKEN_QUOTA_PER_USER: int = Field(default=100000, ge=1000)

    # 日志级别
    LOG_LEVEL: str = Field(default="INFO", pattern=r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")

    # 环境
    ENVIRONMENT: str = Field(default="development", pattern=r"^(development|staging|production)$")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    @field_validator("CORS_ORIGINS")
    @classmethod
    def parse_cors_origins(cls, v: str) -> list[str]:
        """将逗号分隔的字符串解析为列表"""
        return [origin.strip() for origin in v.split(",")]

    @field_validator("ALLOWED_EXTENSIONS")
    @classmethod
    def parse_allowed_extensions(cls, v: str) -> list[str]:
        """将逗号分隔的字符串解析为列表"""
        return [ext.strip().lower() for ext in v.split(",")]

    @field_validator("DATABASE_URL")
    @classmethod
    def validate_database_url(cls, v: str) -> str:
        """验证数据库 URL 格式"""
        if not re.match(r"^postgresql(\+[a-z]+)?://", v):
            raise ValueError("DATABASE_URL 必须是 PostgreSQL 连接字符串")
        return v

    @field_validator("REDIS_URL", "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND")
    @classmethod
    def validate_redis_url(cls, v: str) -> str:
        """验证 Redis URL 格式"""
        if not re.match(r"^redis://", v):
            raise ValueError("Redis URL 必须以 redis:// 开头")
        return v

    @field_validator("LLM_BASE_URL")
    @classmethod
    def validate_http_url(cls, v: str) -> str:
        """验证 HTTP URL 格式"""
        if not re.match(r"^https?://", v):
            raise ValueError("URL 必须以 http:// 或 https:// 开头")
        return v

    @property
    def max_upload_size_bytes(self) -> int:
        """获取最大上传大小（字节）"""
        return self.MAX_UPLOAD_SIZE_MB * 1024 * 1024

    @property
    def is_production(self) -> bool:
        """判断是否为生产环境"""
        return self.ENVIRONMENT == "production"


# 全局配置实例
settings = Settings()
