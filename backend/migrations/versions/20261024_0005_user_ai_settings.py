"""Private, encrypted model connections owned by individual users."""

import sqlalchemy as sa
from alembic import op

revision = "20261024_0005"
down_revision = "20261020_0004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "platform_user_ai_settings",
        sa.Column("user_id", sa.String(36), sa.ForeignKey("platform_users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("id", sa.String(36), nullable=False, unique=True),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("encrypted_key", sa.Text(), nullable=False),
        sa.Column("vision", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade():
    op.drop_table("platform_user_ai_settings")
