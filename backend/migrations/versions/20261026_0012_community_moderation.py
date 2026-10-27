"""Community reporting, durable review decisions and soft content removal."""

import sqlalchemy as sa
from alembic import op

revision = "20261026_0012"
down_revision = "20261026_0011"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("platform_community_posts", "platform_community_comments"):
        op.add_column(table, sa.Column("is_hidden", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        "platform_community_reports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("reporter_id", sa.String(36), sa.ForeignKey("platform_users.id", ondelete="SET NULL")),
        sa.Column("reporter_name", sa.String(64), nullable=False),
        sa.Column("target_type", sa.String(16), nullable=False),
        sa.Column("target_id", sa.String(36), nullable=False),
        sa.Column("post_id", sa.String(36), nullable=False),
        sa.Column("target_title", sa.String(100), nullable=False),
        sa.Column("target_content", sa.Text(), nullable=False),
        sa.Column("reason", sa.String(16), nullable=False),
        sa.Column("details", sa.String(1000), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("reviewer_id", sa.String(36), sa.ForeignKey("platform_users.id", ondelete="SET NULL")),
        sa.Column("reviewer_name", sa.String(64)),
        sa.Column("reviewed_at", sa.DateTime()),
        sa.Column("review_note", sa.String(1000), nullable=False),
        sa.UniqueConstraint("reporter_id", "target_type", "target_id", name="uq_platform_community_report_target"),
        sa.CheckConstraint("target_type IN ('post', 'comment')", name="platform_community_report_target_type"),
        sa.CheckConstraint("reason IN ('spam', 'abuse', 'privacy', 'illegal', 'other')", name="platform_community_report_reason"),
        sa.CheckConstraint("status IN ('pending', 'resolved', 'dismissed')", name="platform_community_report_status"),
    )
    op.create_index("ix_platform_community_report_status_created", "platform_community_reports", ["status", "created_at", "id"])
    op.create_index("ix_platform_community_report_reporter_created", "platform_community_reports", ["reporter_id", "created_at", "id"])
    op.create_index("ix_platform_community_report_post", "platform_community_reports", ["post_id"])
    op.create_index("ix_platform_community_report_target", "platform_community_reports", ["target_type", "target_id"])


def downgrade():
    op.drop_table("platform_community_reports")
    for table in ("platform_community_comments", "platform_community_posts"):
        op.drop_column(table, "is_hidden")
