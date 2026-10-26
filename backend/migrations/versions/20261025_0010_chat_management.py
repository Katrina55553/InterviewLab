"""Persist per-user chat pinning alongside existing conversation titles."""

import sqlalchemy as sa
from alembic import op

revision = "20261025_0010"
down_revision = "20261025_0009"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("platform_chats", sa.Column("is_pinned", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    with op.batch_alter_table("platform_chats") as batch:
        batch.drop_column("is_pinned")
