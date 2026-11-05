"""Fixture task, durable command, and atomic application audit."""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(), primary_key=True),
        *[sa.Column(name, sa.String(), nullable=False) for name in
          ("title", "summary", "subsystem", "state")],
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        *[sa.Column(name, sa.String(), nullable=False) for name in
          ("attempt_outcome", "verification", "publication", "eligibility")],
    )
    op.create_table(
        "commands",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("task_id", sa.String(), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("version", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("reviewed_evidence", sa.JSON(), nullable=False),
        sa.Column("reviewed_revision", sa.Integer(), nullable=False),
        *[sa.Column(name, sa.String(), nullable=False) for name in
          ("status", "message", "created_at")],
        sa.Column("finished_at", sa.String()),
        sa.UniqueConstraint("task_id", "version", name="uq_task_review"),
    )
    op.create_table(
        "applications",
        sa.Column("command_id", sa.String(), sa.ForeignKey("commands.id"), primary_key=True),
        sa.Column("task_id", sa.String(), sa.ForeignKey("tasks.id"), nullable=False),
        *[sa.Column(name, sa.String(), nullable=False) for name in
          ("previous_version", "result_version", "applied_at")],
    )
    op.create_index("ix_commands_pending", "commands", ["status", "created_at"])


def downgrade():
    op.drop_table("applications")
    op.drop_table("commands")
    op.drop_table("tasks")
