"""Finite, process-independent operational counters."""

import sqlalchemy as sa
from alembic import op

revision = "20261020_0003"
down_revision = "20261020_0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("platform_operation_counters",
        sa.Column("id", sa.String(100), primary_key=True), sa.Column("name", sa.String(32), nullable=False),
        sa.Column("scene", sa.String(24), nullable=False), sa.Column("workload", sa.String(24), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False), sa.Column("duration_ms", sa.Integer(), nullable=False))


def downgrade():
    raise RuntimeError("应用回退保留兼容 schema，不自动删除监控记录")
