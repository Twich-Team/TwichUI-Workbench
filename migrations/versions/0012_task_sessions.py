"""One explicit site-approved task session; no execution or permission seed."""

from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "task_sessions",
        sa.Column("id", sa.String, sa.ForeignKey("proposal_decisions.id"), primary_key=True),
        sa.Column("task_id", sa.String, sa.ForeignKey("backlog_items.id"), nullable=False),
        sa.Column("snapshot", sa.JSON, nullable=False),
        sa.Column("binding", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("message", sa.String, nullable=False),
        sa.Column("state", sa.JSON, nullable=False),
        sa.Column("created_at", sa.String, nullable=False),
        sa.Column("finished_at", sa.String),
    )


def downgrade():
    if op.get_bind().execute(sa.text("SELECT count(*) FROM task_sessions")).scalar():
        raise RuntimeError("Retain task-session history; nonempty downgrade refused")
    op.drop_table("task_sessions")
