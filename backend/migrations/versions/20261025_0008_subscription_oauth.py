"""Private subscription grants and single-use OAuth authorization attempts."""

import sqlalchemy as sa
from alembic import op

revision = "20261025_0008"
down_revision = "20261024_0007"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("platform_user_model_credentials", sa.Column("auth_method", sa.String(16), nullable=False, server_default="api-key"))
    op.add_column("platform_user_model_credentials", sa.Column("oauth_scope", sa.String(64)))
    op.add_column("platform_user_model_credentials", sa.Column("refresh_owner", sa.String(36)))
    op.add_column("platform_user_model_credentials", sa.Column("refresh_until", sa.DateTime()))
    op.create_table("platform_user_oauth_hosts",
        sa.Column("user_id", sa.String(36), sa.ForeignKey("platform_users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("host_id", sa.String(36), nullable=False))
    op.create_table("platform_user_oauth_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("platform_users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider_id", sa.String(64), nullable=False),
        sa.Column("state_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("encrypted_data", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False))
    op.create_index("ix_platform_user_oauth_sessions_user_id", "platform_user_oauth_sessions", ["user_id"])


def downgrade():
    op.drop_table("platform_user_oauth_sessions")
    op.drop_table("platform_user_oauth_hosts")
    for name in ("refresh_until", "refresh_owner", "oauth_scope", "auth_method"):
        op.drop_column("platform_user_model_credentials", name)
