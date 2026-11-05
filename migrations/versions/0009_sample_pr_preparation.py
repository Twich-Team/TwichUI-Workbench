"""Controlled draft preview commands and durable deterministic fake-remote evidence."""
from alembic import op
import sqlalchemy as sa

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('pr_preparations',
        sa.Column('id', sa.String, primary_key=True),
        sa.Column('task_id', sa.String, sa.ForeignKey('backlog_items.id'), nullable=False),
        sa.Column('candidate_id', sa.String, sa.ForeignKey('implementation_candidates.id'), nullable=False),
        sa.Column('binding', sa.String, nullable=False, unique=True),
        sa.Column('payload', sa.JSON, nullable=False), sa.Column('snapshot', sa.JSON, nullable=False),
        sa.Column('status', sa.String, nullable=False), sa.Column('message', sa.String, nullable=False),
        sa.Column('created_at', sa.String, nullable=False), sa.Column('started_at', sa.String),
        sa.Column('finished_at', sa.String), sa.Column('result', sa.JSON))
    op.create_index('ix_pr_preparation_pending', 'pr_preparations', ['status', 'created_at'])
    op.create_table('fake_publications',
        sa.Column('binding', sa.String, primary_key=True),
        sa.Column('request_id', sa.String, sa.ForeignKey('pr_preparations.id'), nullable=False, unique=True),
        sa.Column('evidence', sa.JSON, nullable=False), sa.Column('evidence_sha256', sa.String, nullable=False),
        sa.Column('created_at', sa.String, nullable=False))


def downgrade():
    raise RuntimeError('Sample preparation and simulated publisher history must be retained.')
