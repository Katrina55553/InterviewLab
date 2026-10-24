"""Shared interview notes, one like relation, comments and private server drafts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261024_0006"
down_revision = "20261024_0005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "platform_community_posts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("platform_users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("company", sa.String(50), nullable=False),
        sa.Column("role", sa.String(60), nullable=False),
        sa.Column("direction", sa.String(24), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("outcome", sa.String(24), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("featured", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    for field in ("user_id", "company", "direction"):
        op.create_index(f"ix_platform_community_posts_{field}", "platform_community_posts", [field])
    op.create_index("ix_platform_community_post_created", "platform_community_posts", ["created_at", "id"])
    op.create_table(
        "platform_community_tags",
        sa.Column("post_id", sa.String(36), sa.ForeignKey("platform_community_posts.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("tag", sa.String(32), primary_key=True),
    )
    op.create_index("ix_platform_community_tags_tag", "platform_community_tags", ["tag"])
    op.create_table(
        "platform_community_likes",
        sa.Column("post_id", sa.String(36), sa.ForeignKey("platform_community_posts.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("platform_users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_platform_community_likes_user_id", "platform_community_likes", ["user_id"])
    op.create_table(
        "platform_community_comments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("post_id", sa.String(36), sa.ForeignKey("platform_community_posts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("platform_users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("content", sa.String(2000), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_platform_community_comments_user_id", "platform_community_comments", ["user_id"])
    op.create_index("ix_platform_community_comment_created", "platform_community_comments", ["post_id", "created_at", "id"])
    op.create_table(
        "platform_community_drafts",
        sa.Column("user_id", sa.String(36), sa.ForeignKey("platform_users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("post_id", sa.String(36), nullable=False),
        sa.Column("data", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade():
    for name in ("drafts", "comments", "likes", "tags", "posts"):
        op.drop_table("platform_community_" + name)
