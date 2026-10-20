"""Durable platform records. PostgreSQL is the source of truth for every run."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.core.database import Base


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


json_type = JSON().with_variant(JSONB(), "postgresql")


class User(Base):
    __tablename__ = "platform_users"
    __table_args__ = (CheckConstraint("role IN ('user', 'admin')", name="platform_user_role"),)

    id = Column(String(36), primary_key=True, default=new_id)
    username = Column(String(64), unique=True, nullable=False)
    password_hash = Column(Text, nullable=False)
    role = Column(String(16), nullable=False, default="user")
    created_at = Column(DateTime, nullable=False, default=utcnow)


class LoginSession(Base):
    __tablename__ = "platform_login_sessions"

    # Only the SHA-256 digest of the random browser token is ever stored.
    id = Column(String(64), primary_key=True)
    user_id = Column(String(36), ForeignKey("platform_users.id", ondelete="CASCADE"), nullable=False, index=True)
    csrf_token = Column(String(128), nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)


class AuthThrottle(Base):
    __tablename__ = "platform_auth_throttles"

    id = Column(String(128), primary_key=True)
    attempts = Column(Integer, nullable=False, default=0)
    expires_at = Column(DateTime, nullable=False, index=True)


class Resume(Base):
    __tablename__ = "platform_resumes"
    __table_args__ = (
        CheckConstraint("status IN ('queued', 'processing', 'ready', 'failed')", name="platform_resume_status"),
    )

    id = Column(String(36), primary_key=True, default=new_id)
    user_id = Column(String(36), ForeignKey("platform_users.id", ondelete="CASCADE"), nullable=False, index=True)
    filename = Column(String(255), nullable=False)
    file_path = Column(Text, nullable=False)
    status = Column(String(24), nullable=False, default="queued")
    active_job_id = Column(String(36), nullable=True)
    raw_text = Column(Text, nullable=True)
    parsed = Column(json_type, nullable=True)
    preparation = Column(json_type, nullable=True)
    config_snapshot = Column(json_type, nullable=False, default=dict)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)


class Interview(Base):
    __tablename__ = "platform_interviews"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ready', 'active', 'reporting', 'completed', 'failed')", name="platform_interview_status"
        ),
        Index("ix_platform_interview_owner_created", "user_id", "created_at"),
    )

    id = Column(String(36), primary_key=True, default=new_id)
    user_id = Column(String(36), ForeignKey("platform_users.id", ondelete="CASCADE"), nullable=False)
    resume_id = Column(String(36), ForeignKey("platform_resumes.id", ondelete="RESTRICT"), nullable=False, index=True)
    status = Column(String(24), nullable=False, default="ready")
    active_job_id = Column(String(36), nullable=True)
    version = Column(Integer, nullable=False, default=0)
    snapshot = Column(json_type, nullable=False, default=dict)
    config_snapshot = Column(json_type, nullable=False, default=dict)
    report = Column(json_type, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class Command(Base):
    __tablename__ = "platform_commands"
    __table_args__ = (
        UniqueConstraint("interview_id", "request_id", name="uq_platform_command_request"),
        CheckConstraint("status IN ('running', 'completed', 'failed', 'unknown')", name="platform_command_status"),
    )

    id = Column(String(36), primary_key=True, default=new_id)
    interview_id = Column(
        String(36), ForeignKey("platform_interviews.id", ondelete="CASCADE"), nullable=False, index=True
    )
    request_id = Column(String(128), nullable=False)
    input_hash = Column(String(64), nullable=False)
    status = Column(String(24), nullable=False, default="running")
    owner_token = Column(String(128), nullable=True)
    lease_until = Column(DateTime, nullable=True)
    result = Column(json_type, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)


class Event(Base):
    __tablename__ = "platform_events"
    __table_args__ = (UniqueConstraint("interview_id", "sequence", name="uq_platform_event_sequence"),)

    id = Column(String(36), primary_key=True, default=new_id)
    interview_id = Column(
        String(36), ForeignKey("platform_interviews.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence = Column(Integer, nullable=False)
    payload = Column(json_type, nullable=False)
    created_at = Column(DateTime, nullable=False, default=utcnow)


class Outbox(Base):
    __tablename__ = "platform_outbox"
    __table_args__ = (CheckConstraint("status IN ('pending', 'published')", name="platform_outbox_status"),)

    id = Column(String(36), primary_key=True, default=new_id)
    kind = Column(String(64), nullable=False)
    aggregate_id = Column(String(36), nullable=False)
    payload = Column(json_type, nullable=False, default=dict)
    status = Column(String(24), nullable=False, default="pending", index=True)
    execution_status = Column(String(24), nullable=False, default="queued")
    owner_token = Column(String(128), nullable=True)
    lease_until = Column(DateTime, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)


class Chat(Base):
    __tablename__ = "platform_chats"

    id = Column(String(36), primary_key=True, default=new_id)
    user_id = Column(String(36), ForeignKey("platform_users.id", ondelete="CASCADE"), nullable=False, index=True)
    agent_id = Column(String(36), ForeignKey("platform_agents.id", ondelete="SET NULL"), nullable=True)
    title = Column(String(255), nullable=False, default="新对话")
    active_request_id = Column(String(128), nullable=True)
    owner_token = Column(String(128), nullable=True)
    lease_until = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)


class ChatMessage(Base):
    __tablename__ = "platform_chat_messages"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant', 'system', 'tool')", name="platform_chat_message_role"),
        UniqueConstraint("chat_id", "role", "request_id", name="uq_platform_chat_request_role"),
    )

    id = Column(String(36), primary_key=True, default=new_id)
    chat_id = Column(String(36), ForeignKey("platform_chats.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String(24), nullable=False)
    content = Column(Text, nullable=False, default="")
    status = Column(String(24), nullable=False, default="completed")
    request_id = Column(String(128), nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)


class Provider(Base):
    __tablename__ = "platform_providers"

    id = Column(String(36), primary_key=True, default=new_id)
    name = Column(String(100), nullable=False)
    base_url = Column(Text, nullable=False)
    model = Column(String(200), nullable=False)
    encrypted_key = Column(Text, nullable=True)
    enabled = Column(Boolean, nullable=False, default=True)
    vision = Column(Boolean, nullable=False, default=False)
    version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, nullable=False, default=utcnow)


class Agent(Base):
    __tablename__ = "platform_agents"
    __table_args__ = (
        CheckConstraint(
            "scene IS NULL OR scene IN ('resume', 'questions', 'evaluate', 'followup', 'report', 'chat', 'vision')",
            name="platform_agent_scene",
        ),
    )

    id = Column(String(36), primary_key=True, default=new_id)
    name = Column(String(100), nullable=False)
    scene = Column(String(24), nullable=True, unique=True)
    provider_id = Column(
        String(36), ForeignKey("platform_providers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    prompt = Column(Text, nullable=False, default="")
    tools = Column(json_type, nullable=False, default=list)
    enabled = Column(Boolean, nullable=False, default=True)
    version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, nullable=False, default=utcnow)


class AiCall(Base):
    """Usage metadata without prompts, resume contents, or provider secrets."""

    __tablename__ = "platform_ai_calls"

    id = Column(String(36), primary_key=True, default=new_id)
    user_id = Column(String(36), ForeignKey("platform_users.id", ondelete="SET NULL"), nullable=True, index=True)
    interview_id = Column(
        String(36), ForeignKey("platform_interviews.id", ondelete="SET NULL"), nullable=True, index=True
    )
    scene = Column(String(24), nullable=False)
    provider_id = Column(String(36), nullable=True)
    model = Column(String(200), nullable=True)
    status = Column(String(24), nullable=False)
    duration_ms = Column(Integer, nullable=False, default=0)
    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    error_code = Column(String(64), nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)
