"""Separate private authentication from the user's active model selection."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261024_0007"
down_revision = "20261024_0006"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("platform_user_ai_settings", sa.Column("selection", sa.JSON().with_variant(JSONB(), "postgresql")))
    op.create_table(
        "platform_user_model_credentials",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("platform_users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider_id", sa.String(2100), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("encrypted_key", sa.Text()),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint("user_id", "provider_id", name="uq_platform_user_model_credential"),
    )
    op.create_index("ix_platform_user_model_credentials_user_id", "platform_user_model_credentials", ["user_id"])


def downgrade():
    op.drop_table("platform_user_model_credentials")
    op.drop_column("platform_user_ai_settings", "selection")
