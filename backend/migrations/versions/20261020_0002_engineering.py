"""Add durable governance and delivery metadata; preserve all platform facts."""

import json
import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261020_0002"
down_revision = "20261015_0001"
branch_labels = None
depends_on = None
JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade():
    for name, kind in [("trace_id", sa.String(32)), ("delivery_owner", sa.String(36)),
                       ("delivery_lease_until", sa.DateTime()), ("delivery_id", sa.String(36)),
                       ("published_at", sa.DateTime()), ("next_delivery_at", sa.DateTime())]:
        op.add_column("platform_outbox", sa.Column(name, kind, nullable=True))
    op.add_column("platform_outbox", sa.Column("delivery_attempts", sa.Integer(), nullable=False, server_default="0"))
    op.create_index("ix_platform_outbox_next_delivery_at", "platform_outbox", ["next_delivery_at"])
    for name, kind in [("trace_id", sa.String(32)), ("flight_id", sa.String(64)),
                       ("credential_scope", sa.String(64)), ("workload", sa.String(24)),
                       ("outcome", sa.String(16)), ("lease_until", sa.DateTime())]:
        op.add_column("platform_ai_calls", sa.Column(name, kind, nullable=True))
    op.add_column("platform_ai_calls", sa.Column("reserved_tokens", sa.Integer(), nullable=False, server_default="0"))
    # Carry recorded usage into the new reservation ledger; do not erase daily usage.
    # Historical unknown input/output amounts cannot be reconstructed.
    op.execute(sa.text("UPDATE platform_ai_calls SET reserved_tokens = COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0)"))
    for name in ("flight_id", "credential_scope"):
        op.create_index("ix_platform_ai_calls_" + name, "platform_ai_calls", [name])
    op.create_table("platform_ai_permits",
                    sa.Column("id", sa.String(36), primary_key=True),
                    sa.Column("credential_scope", sa.String(64), nullable=False),
                    sa.Column("workload", sa.String(24), nullable=False),
                    sa.Column("owner_token", sa.String(36), nullable=False),
                    sa.Column("lease_until", sa.DateTime(), nullable=False))
    op.create_index("ix_platform_ai_permits_credential_scope", "platform_ai_permits", ["credential_scope"])
    op.create_index("ix_platform_ai_permits_lease_until", "platform_ai_permits", ["lease_until"])
    op.create_table("platform_ai_flights",
                    sa.Column("id", sa.String(64), primary_key=True),
                    sa.Column("operation_id", sa.String(36), nullable=False),
                    sa.Column("generation", sa.Integer(), nullable=False),
                    sa.Column("previous_id", sa.String(64)),
                    sa.Column("status", sa.String(24), nullable=False),
                    sa.Column("owner_token", sa.String(36)), sa.Column("lease_until", sa.DateTime()),
                    sa.Column("result", JSON), sa.Column("error", JSON), sa.Column("trace_id", sa.String(32)),
                    sa.Column("created_at", sa.DateTime(), nullable=False))
    op.create_index("ix_platform_ai_flights_operation_id", "platform_ai_flights", ["operation_id"])
    op.create_index("ix_platform_ai_flights_lease_until", "platform_ai_flights", ["lease_until"])
    op.create_table("platform_config_revisions",
                    sa.Column("id", sa.String(36), primary_key=True),
                    sa.Column("kind", sa.String(16), nullable=False),
                    sa.Column("entity_id", sa.String(36), nullable=False),
                    sa.Column("version", sa.Integer(), nullable=False), sa.Column("actor_id", sa.String(36)),
                    sa.Column("config", JSON, nullable=False), sa.Column("deleted", sa.Boolean(), nullable=False),
                    sa.Column("created_at", sa.DateTime(), nullable=False),
                    sa.UniqueConstraint("kind", "entity_id", "version", name="uq_platform_revision"))
    op.create_index("ix_platform_config_revisions_entity_id", "platform_config_revisions", ["entity_id"])
    op.create_table("platform_instance_heartbeats",
                    sa.Column("id", sa.String(128), primary_key=True),
                    sa.Column("role", sa.String(24), nullable=False), sa.Column("last_seen", sa.DateTime(), nullable=False),
                    sa.Column("last_progress", sa.DateTime()), sa.Column("ready", sa.Boolean(), nullable=False))
    # Baselines are recorded at migration time with no invented operator/history.
    connection = op.get_bind()
    revisions = sa.table("platform_config_revisions", sa.column("id"), sa.column("kind"), sa.column("entity_id"),
                         sa.column("version"), sa.column("config", JSON), sa.column("deleted"), sa.column("created_at"))
    for kind, table in (("provider", "platform_providers"), ("agent", "platform_agents")):
        for row in connection.execute(sa.text("SELECT * FROM " + table)).mappings():
            config = {k: v for k, v in row.items() if k != "created_at"}
            if kind == "agent" and isinstance(config.get("tools"), str):
                config["tools"] = json.loads(config["tools"])
            connection.execute(revisions.insert().values(id=str(uuid.uuid4()), kind=kind, entity_id=row["id"],
                version=row["version"], config=config, deleted=False, created_at=datetime.now(UTC).replace(tzinfo=None)))


def downgrade():
    raise RuntimeError("工程化迁移只向前升级；应用回滚保留兼容 schema，禁止自动删除审计与调用记录")
