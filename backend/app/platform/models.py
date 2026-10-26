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
    false,
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


class UserAISettings(Base):
    """One private model connection per account; credentials never leave the server."""

    __tablename__ = "platform_user_ai_settings"

    user_id = Column(String(36), ForeignKey("platform_users.id", ondelete="CASCADE"), primary_key=True)
    id = Column(String(36), nullable=False, unique=True, default=new_id)
    base_url = Column(Text, nullable=False)
    model = Column(String(200), nullable=False)
    encrypted_key = Column(Text, nullable=False)
    vision = Column(Boolean, nullable=False, default=False)
    version = Column(Integer, nullable=False, default=1)
    selection = Column(json_type, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class UserVisionSettings(Base):
    """An independently tested visual model; selections contain no credentials."""

    __tablename__ = "platform_user_vision_settings"

    user_id = Column(String(36), ForeignKey("platform_users.id", ondelete="CASCADE"), primary_key=True)
    selection = Column(json_type, nullable=True)
    enabled = Column(Boolean, nullable=False, default=False)
    version = Column(Integer, nullable=False, default=1)
    probe_fingerprint = Column(String(64), nullable=True)
    tested_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class UserModelCredential(Base):
    __tablename__ = "platform_user_model_credentials"
    __table_args__ = (UniqueConstraint("user_id", "provider_id", name="uq_platform_user_model_credential"),)

    id = Column(String(36), primary_key=True, default=new_id)
    user_id = Column(String(36), ForeignKey("platform_users.id", ondelete="CASCADE"), nullable=False, index=True)
    provider_id = Column(String(2100), nullable=False)
    base_url = Column(Text, nullable=False)
    encrypted_key = Column(Text, nullable=True)
    version = Column(Integer, nullable=False, default=1)
    auth_method = Column(String(16), nullable=False, default="api-key")
    oauth_scope = Column(String(64), nullable=True)
    refresh_owner = Column(String(36), nullable=True)
    refresh_until = Column(DateTime, nullable=True)


class UserOAuthHost(Base):
    __tablename__ = "platform_user_oauth_hosts"

    user_id = Column(String(36), ForeignKey("platform_users.id", ondelete="CASCADE"), primary_key=True)
    host_id = Column(String(36), nullable=False, default=new_id)


class UserOAuthSession(Base):
    __tablename__ = "platform_user_oauth_sessions"

    id = Column(String(36), primary_key=True, default=new_id)
    user_id = Column(String(36), ForeignKey("platform_users.id", ondelete="CASCADE"), nullable=False, index=True)
    provider_id = Column(String(64), nullable=False)
    state_hash = Column(String(64), nullable=False, unique=True)
    status = Column(String(20), nullable=False, default="pending")
    encrypted_data = Column(Text, nullable=False)
    expires_at = Column(DateTime, nullable=False)
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
    demo = Column(Boolean, nullable=False, default=False)
    config_snapshot = Column(json_type, nullable=False, default=dict)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)


class ResumeDiagnosis(Base):
    __tablename__ = "platform_resume_diagnoses"
    __table_args__ = (
        CheckConstraint("status IN ('queued', 'processing', 'ready', 'failed')", name="platform_diagnosis_status"),
    )

    id = Column(String(36), primary_key=True, default=new_id)
    resume_id = Column(String(36), ForeignKey("platform_resumes.id", ondelete="CASCADE"), nullable=False, unique=True)
    user_id = Column(String(36), ForeignKey("platform_users.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String(24), nullable=False, default="queued")
    active_job_id = Column(String(36), nullable=True)
    result = Column(json_type, nullable=True)
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
    trace_id = Column(String(32), nullable=True)
    delivery_owner = Column(String(36), nullable=True)
    delivery_lease_until = Column(DateTime, nullable=True)
    delivery_attempts = Column(Integer, nullable=False, default=0, server_default="0")
    delivery_id = Column(String(36), nullable=True)
    published_at = Column(DateTime, nullable=True)
    next_delivery_at = Column(DateTime, nullable=True, index=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)


class Chat(Base):
    __tablename__ = "platform_chats"

    id = Column(String(36), primary_key=True, default=new_id)
    user_id = Column(String(36), ForeignKey("platform_users.id", ondelete="CASCADE"), nullable=False, index=True)
    agent_id = Column(String(36), ForeignKey("platform_agents.id", ondelete="SET NULL"), nullable=True)
    title = Column(String(255), nullable=False, default="新对话")
    is_pinned = Column(Boolean, nullable=False, default=False, server_default=false())
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
            "scene IS NULL OR scene IN ('parse', 'resume', 'questions', 'evaluate', 'followup', 'report', 'chat', 'vision')",
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
    trace_id = Column(String(32), nullable=True)
    flight_id = Column(String(64), nullable=True, index=True)
    credential_scope = Column(String(64), nullable=True, index=True)
    workload = Column(String(24), nullable=True)
    reserved_tokens = Column(Integer, nullable=False, default=0, server_default="0")
    outcome = Column(String(16), nullable=True)
    lease_until = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)


class AiPermit(Base):
    __tablename__ = "platform_ai_permits"

    id = Column(String(36), primary_key=True, default=new_id)
    credential_scope = Column(String(64), nullable=False, index=True)
    workload = Column(String(24), nullable=False)
    owner_token = Column(String(36), nullable=False)
    lease_until = Column(DateTime, nullable=False, index=True)


class AiFlight(Base):
    __tablename__ = "platform_ai_flights"

    id = Column(String(64), primary_key=True)
    operation_id = Column(String(36), nullable=False, index=True)
    generation = Column(Integer, nullable=False, default=0)
    previous_id = Column(String(64), nullable=True)
    status = Column(String(24), nullable=False)
    owner_token = Column(String(36), nullable=True)
    lease_until = Column(DateTime, nullable=True, index=True)
    result = Column(json_type, nullable=True)
    error = Column(json_type, nullable=True)
    trace_id = Column(String(32), nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)


class ConfigRevision(Base):
    __tablename__ = "platform_config_revisions"
    __table_args__ = (UniqueConstraint("kind", "entity_id", "version", name="uq_platform_revision"),)

    id = Column(String(36), primary_key=True, default=new_id)
    kind = Column(String(16), nullable=False)
    entity_id = Column(String(36), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    actor_id = Column(String(36), nullable=True)
    config = Column(json_type, nullable=False)
    deleted = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=utcnow)


class InstanceHeartbeat(Base):
    __tablename__ = "platform_instance_heartbeats"

    id = Column(String(128), primary_key=True)
    role = Column(String(24), nullable=False)
    last_seen = Column(DateTime, nullable=False)
    last_progress = Column(DateTime, nullable=True)
    ready = Column(Boolean, nullable=False, default=False)


class OperationCounter(Base):
    __tablename__ = "platform_operation_counters"

    id = Column(String(100), primary_key=True)
    name = Column(String(32), nullable=False)
    scene = Column(String(24), nullable=False)
    workload = Column(String(24), nullable=False)
    value = Column(Integer, nullable=False, default=0)
    duration_ms = Column(Integer, nullable=False, default=0)


class CircuitObservation(Base):
    __tablename__ = "platform_circuit_observations"

    id = Column(String(200), primary_key=True)
    scene = Column(String(24), nullable=False)
    state = Column(String(16), nullable=False)
    valid_until = Column(DateTime, nullable=False)
    last_seen = Column(DateTime, nullable=False)


class CommunityPost(Base):
    __tablename__ = "platform_community_posts"
    __table_args__ = (Index("ix_platform_community_post_created", "created_at", "id"),)

    id = Column(String(36), primary_key=True, default=new_id)
    user_id = Column(String(36), ForeignKey("platform_users.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(100), nullable=False)
    company = Column(String(50), nullable=False, index=True)
    role = Column(String(60), nullable=False)
    direction = Column(String(24), nullable=False, index=True)
    stage = Column(String(32), nullable=False)
    outcome = Column(String(24), nullable=False)
    content = Column(Text, nullable=False)
    featured = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=utcnow)


class CommunityTag(Base):
    __tablename__ = "platform_community_tags"

    post_id = Column(String(36), ForeignKey("platform_community_posts.id", ondelete="CASCADE"), primary_key=True)
    tag = Column(String(32), primary_key=True, index=True)


class CommunityLike(Base):
    __tablename__ = "platform_community_likes"

    post_id = Column(String(36), ForeignKey("platform_community_posts.id", ondelete="CASCADE"), primary_key=True)
    user_id = Column(String(36), ForeignKey("platform_users.id", ondelete="CASCADE"), primary_key=True, index=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)


class CommunityComment(Base):
    __tablename__ = "platform_community_comments"
    __table_args__ = (Index("ix_platform_community_comment_created", "post_id", "created_at", "id"),)

    id = Column(String(36), primary_key=True, default=new_id)
    post_id = Column(String(36), ForeignKey("platform_community_posts.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(String(36), ForeignKey("platform_users.id", ondelete="CASCADE"), nullable=False, index=True)
    content = Column(String(2000), nullable=False)
    created_at = Column(DateTime, nullable=False, default=utcnow)


class CommunityDraft(Base):
    __tablename__ = "platform_community_drafts"

    user_id = Column(String(36), ForeignKey("platform_users.id", ondelete="CASCADE"), primary_key=True)
    post_id = Column(String(36), nullable=False)
    data = Column(json_type, nullable=False)
    version = Column(Integer, nullable=False, default=1)
    updated_at = Column(DateTime, nullable=False, default=utcnow)
