"""Persist job timings and bounded-lived process circuit observations."""

import sqlalchemy as sa
from alembic import op

revision = "20261020_0004"
down_revision = "20261020_0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("platform_outbox", sa.Column("started_at", sa.DateTime(), nullable=True))
    op.add_column("platform_outbox", sa.Column("finished_at", sa.DateTime(), nullable=True))
    op.create_table("platform_circuit_observations",
        sa.Column("id", sa.String(200), primary_key=True), sa.Column("scene", sa.String(24), nullable=False),
        sa.Column("state", sa.String(16), nullable=False), sa.Column("valid_until", sa.DateTime(), nullable=False),
        sa.Column("last_seen", sa.DateTime(), nullable=False))


def downgrade():
    raise RuntimeError("应用回退保留兼容 schema，禁止删除调用观测历史")
