"""Verified email identities and durable single-use mail challenges."""

import sqlalchemy as sa
from alembic import op

revision = "20261026_0013"
down_revision = "20261026_0012"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("platform_users", sa.Column("email", sa.String(254), nullable=True))
    op.create_index("uq_platform_users_email", "platform_users", ["email"], unique=True)
    op.create_table(
        "platform_email_challenges",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("platform_users.id", ondelete="CASCADE")),
        sa.Column("generation", sa.String(36), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("resend_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("purpose IN ('register', 'reset', 'bind')", name="platform_email_challenge_purpose"),
        sa.CheckConstraint("status IN ('pending', 'sent', 'consumed')", name="platform_email_challenge_status"),
    )
    op.create_index("ix_platform_email_challenges_expires_at", "platform_email_challenges", ["expires_at"])


def downgrade():
    op.drop_table("platform_email_challenges")
    op.drop_index("uq_platform_users_email", table_name="platform_users")
    op.drop_column("platform_users", "email")
