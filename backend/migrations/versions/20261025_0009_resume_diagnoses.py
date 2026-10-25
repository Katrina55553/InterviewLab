"""Separate parsed resume facts from optional diagnosis state and results."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261025_0009"
down_revision = "20261025_0008"
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(JSONB(), "postgresql")
OLD_SCENES = "'resume', 'questions', 'evaluate', 'followup', 'report', 'chat', 'vision'"


def upgrade():
    op.add_column("platform_resumes", sa.Column("demo", sa.Boolean(), nullable=False, server_default=sa.false()))
    diagnoses = op.create_table(
        "platform_resume_diagnoses",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("resume_id", sa.String(36), sa.ForeignKey("platform_resumes.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("platform_users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("active_job_id", sa.String(36), nullable=True),
        sa.Column("result", JSON, nullable=True),
        sa.Column("config_snapshot", JSON, nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("status IN ('queued', 'processing', 'ready', 'failed')", name="platform_diagnosis_status"),
    )
    op.create_index("ix_platform_resume_diagnoses_user_id", "platform_resume_diagnoses", ["user_id"])
    resumes = sa.table("platform_resumes", sa.column("id", sa.String()), sa.column("user_id", sa.String()),
        sa.column("preparation", JSON), sa.column("config_snapshot", JSON), sa.column("created_at", sa.DateTime()),
        sa.column("demo", sa.Boolean()))
    connection = op.get_bind()
    # Preserve existing real results and their original frozen credentials; no AI runs.
    for row in connection.execute(sa.select(resumes).where(resumes.c.preparation.is_not(None))).mappings():
        result = row["preparation"]
        if not isinstance(result, dict):
            continue
        connection.execute(diagnoses.insert().values(id=row["id"], resume_id=row["id"], user_id=row["user_id"],
            status="ready", result=result, config_snapshot=row["config_snapshot"], created_at=row["created_at"]))
        connection.execute(resumes.update().where(resumes.c.id == row["id"]).values(demo=bool(result.get("demo"))))
    with op.batch_alter_table("platform_resumes") as batch:
        batch.drop_column("preparation")
    with op.batch_alter_table("platform_agents") as batch:
        batch.drop_constraint("platform_agent_scene", type_="check")
        batch.create_check_constraint("platform_agent_scene", f"scene IS NULL OR scene IN ('parse', {OLD_SCENES})")


def downgrade():
    op.add_column("platform_resumes", sa.Column("preparation", JSON, nullable=True))
    op.execute(sa.text("UPDATE platform_resumes SET preparation = (SELECT result FROM platform_resume_diagnoses "
                       "WHERE resume_id = platform_resumes.id AND status = 'ready')"))
    op.drop_index("ix_platform_resume_diagnoses_user_id", table_name="platform_resume_diagnoses")
    op.drop_table("platform_resume_diagnoses")
    with op.batch_alter_table("platform_resumes") as batch:
        batch.drop_column("demo")
    op.execute(sa.text("UPDATE platform_agents SET scene = NULL WHERE scene = 'parse'"))
    with op.batch_alter_table("platform_agents") as batch:
        batch.drop_constraint("platform_agent_scene", type_="check")
        batch.create_check_constraint("platform_agent_scene", f"scene IS NULL OR scene IN ({OLD_SCENES})")
