"""Media metadata only: microphone audio and camera frames are never retained."""

from sqlalchemy import Column, DateTime, ForeignKey, String, Text, UniqueConstraint

from app.core.database import Base
from app.platform.models import new_id, utcnow


class MediaTask(Base):
    __tablename__ = "platform_media_tasks"
    __table_args__ = (UniqueConstraint("user_id", "interview_id", "text_hash", name="uq_platform_tts_text"),)

    id = Column(String(36), primary_key=True, default=new_id)
    user_id = Column(String(36), ForeignKey("platform_users.id", ondelete="CASCADE"), nullable=False, index=True)
    interview_id = Column(String(36), ForeignKey("platform_interviews.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String(24), nullable=False, default="creating")
    provider_task_id = Column(String(128), nullable=True)
    audio_path = Column(Text, nullable=True)
    text_hash = Column(String(64), nullable=False)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)


class VisionFeedback(Base):
    __tablename__ = "platform_vision_feedback"

    id = Column(String(36), primary_key=True, default=new_id)
    interview_id = Column(String(36), ForeignKey("platform_interviews.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("platform_users.id", ondelete="CASCADE"), nullable=False, index=True)
    provider_id = Column(String(36), nullable=False)
    provider_version = Column(String(32), nullable=False)
    feedback = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=utcnow)
