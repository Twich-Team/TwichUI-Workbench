"""Exact capability offers and durable real PR delivery; no data or permission seed."""

from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "task_run_offers",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("proposal_id", sa.String, sa.ForeignKey("proposals.id"), nullable=False),
        sa.Column("fingerprint", sa.String, nullable=False, unique=True),
        sa.Column("snapshot", sa.JSON, nullable=False),
        sa.Column("created_at", sa.String, nullable=False),
    )
    op.create_table(
        "real_publications",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column(
            "execution_id",
            sa.String,
            sa.ForeignKey("real_execution_requests.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("task_id", sa.String, sa.ForeignKey("backlog_items.id"), nullable=False),
        sa.Column("binding", sa.String, nullable=False, unique=True),
        sa.Column("snapshot", sa.JSON, nullable=False),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("message", sa.String, nullable=False),
        sa.Column("state", sa.JSON, nullable=False),
        sa.Column("created_at", sa.String, nullable=False),
        sa.Column("finished_at", sa.String),
    )


def downgrade():
    raise RuntimeError("Task approvals and delivery history must be preserved")
