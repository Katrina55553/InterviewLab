"""Independent personal visual model selections and image probe verification."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261026_0011"
down_revision = "20261025_0010"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "platform_user_vision_settings",
        sa.Column("user_id", sa.String(36), sa.ForeignKey("platform_users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("selection", sa.JSON().with_variant(JSONB(), "postgresql")),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("probe_fingerprint", sa.String(64)),
        sa.Column("tested_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade():
    op.drop_table("platform_user_vision_settings")
