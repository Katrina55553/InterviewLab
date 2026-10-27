"""应用配置模块

使用 pydantic-settings 从环境变量读取配置。
支持 .env 文件和系统环境变量。
"""

import re
from email.headerregistry import Address
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置类"""

    # 数据库配置
    DATABASE_URL: str = "postgresql+asyncpg://user:password@localhost:5432/resume_agent"

    # Redis 配置
    REDIS_URL: str = "redis://localhost:6379/0"

    # 可选：公开仓库统计默认匿名拉取，Token 仅在后端使用。
    GITHUB_TOKEN: str = ""

    # LLM API（OpenAI 兼容格式，支持 DeepSeek 等第三方）
    LLM_BASE_URL: str = "https://api.deepseek.com"
    LLM_API_KEY: str = ""
    LLM_MODEL: str = "deepseek-chat"
    LLM_MODEL_LIGHT: str = "deepseek-chat"
    LLM_MODEL_HEAVY: str = "deepseek-reasoner"
    # User-entered endpoints must be explicitly trusted; never expose an arbitrary HTTP proxy.
    USER_LLM_ALLOWED_BASE_URLS: str = (
        "https://api.deepseek.com,https://api.deepseek.com/v1,https://api.openai.com/v1,"
        "https://openrouter.ai/api/v1,https://api.siliconflow.cn/v1,"
        "https://dashscope.aliyuncs.com/compatible-mode/v1,https://api.moonshot.cn/v1,"
        "https://open.bigmodel.cn/api/paas/v4"
    )
    USER_LLM_OAUTH_REDIRECT_URI: str = "http://127.0.0.1:8000/api/settings/ai/oauth/callback"
    USER_LLM_OAUTH_CLIENT_ID: str = ""

    # 安全配置
    SECRET_KEY: str = Field(default="change-me-in-production", min_length=16)

    # 新平台：默认调用真实服务；演示降级需要显式打开。
    PLATFORM_DEMO_MODE: bool = False
    AUTH_SESSION_HOURS: int = Field(default=168, ge=1, le=720)
    AUTH_COOKIE_SECURE: bool = False
    AUTH_RATE_LIMIT_WINDOW_SECONDS: int = Field(default=900, ge=60, le=86400)
    AUTH_MAX_ATTEMPTS: int = Field(default=10, ge=1, le=100)
    AUTH_IP_MAX_ATTEMPTS: int = Field(default=60, ge=1, le=1000)

    # 通用 SMTP：QQ 邮箱使用 ssl / 465，其他服务商也可使用 starttls。
    SMTP_HOST: str = ""
    SMTP_PORT: int = Field(default=465, ge=1, le=65535)
    SMTP_SECURITY: Literal["ssl", "starttls"] = "ssl"
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = Field(default="", repr=False)
    SMTP_FROM: str = ""
    SMTP_FROM_NAME: str = "知面"
    SMTP_TIMEOUT_SECONDS: float = Field(default=10, ge=1, le=30)
    AI_TIMEOUT_SECONDS: int = Field(default=90, ge=1, le=600)
    AI_MAX_CONCURRENCY: int = Field(default=4, ge=1, le=64)
    AI_COMMAND_LEASE_SECONDS: int = Field(default=150, ge=10, le=1800)
    AI_MAX_WAITERS: int = Field(default=16, ge=0, le=1000)
    AI_WAIT_SECONDS: float = Field(default=5, gt=0, le=60)
    AI_BACKGROUND_CONCURRENCY: int = Field(default=2, ge=1, le=64)
    AI_VISION_CONCURRENCY: int = Field(default=1, ge=1, le=16)
    AI_PROVIDER_CONCURRENCY: int = Field(default=8, ge=1, le=256)
    AI_INTERACTIVE_RESERVED_PERMITS: int = Field(default=2, ge=0, le=256)
    AI_TOOL_CONCURRENCY: int = Field(default=2, ge=1, le=16)
    AI_MAX_OUTPUT_TOKENS: int = Field(default=4096, ge=100, le=32000)
    AI_CIRCUIT_MIN_CALLS: int = Field(default=10, ge=1, le=20)
    AI_CIRCUIT_OPEN_SECONDS: float = Field(default=30, gt=0, le=600)
    AI_UNKNOWN_LIMIT: int = Field(default=3, ge=1, le=100)
    OUTBOX_REDELIVER_SECONDS: int = Field(default=60, ge=10, le=3600)
    PARSER_PROCESSES: int = Field(default=2, ge=1, le=8)
    MEDIA_MAX_CONNECTIONS: int = Field(default=8, ge=1, le=128)
    SERVICE_ROLE: str = Field(default="backend", pattern=r"^(backend|worker|dispatcher|inspector)$")

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

    @field_validator("SMTP_HOST", "SMTP_USERNAME", "SMTP_FROM", "SMTP_FROM_NAME")
    @classmethod
    def validate_smtp_header_value(cls, value: str) -> str:
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("SMTP 配置不能包含控制字符")
        return value.strip()

    @field_validator("SMTP_HOST")
    @classmethod
    def validate_smtp_host(cls, value: str) -> str:
        if value and ("://" in value or any(char in value for char in "/@?# ")):
            raise ValueError("SMTP_HOST 须为服务器域名或 IP 地址，不包含协议、路径或端口")
        return value

    @field_validator("SMTP_FROM")
    @classmethod
    def validate_smtp_from(cls, value: str) -> str:
        if not value:
            return value
        try:
            address = Address(addr_spec=value)
            if not address.username or not address.domain:
                raise ValueError
        except (ValueError, IndexError) as exc:
            raise ValueError("SMTP_FROM 须为完整的发信邮箱地址") from exc
        return value

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

    def validate_runtime(self) -> None:
        if self.ENVIRONMENT in {"staging", "production"} and (
            self.SECRET_KEY in {"change-me-in-production", "your-secret-key-here-change-in-production"}
            or len(self.SECRET_KEY) < 32
        ):
            raise ValueError("生产环境需要稳定随机的 SECRET_KEY（至少32字符）；禁止使用示例密钥")


# 全局配置实例
settings = Settings()
