"""Approval-bound fixture admission receipts; no execution permission."""
from alembic import op
import sqlalchemy as sa

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('admission_requests',
        sa.Column('id', sa.String, primary_key=True),
        sa.Column('task_id', sa.String, sa.ForeignKey('backlog_items.id'), nullable=False),
        sa.Column('approval_id', sa.String, sa.ForeignKey('proposal_decisions.id'), nullable=False),
        sa.Column('binding', sa.String, nullable=False),
        sa.Column('payload', sa.JSON, nullable=False),
        sa.Column('snapshot', sa.JSON, nullable=False),
        sa.Column('status', sa.String, nullable=False),
        sa.Column('message', sa.String, nullable=False),
        sa.Column('created_at', sa.String, nullable=False),
        sa.Column('finished_at', sa.String),
        sa.UniqueConstraint('task_id', 'binding', name='uq_fixture_admission'))
    op.create_index('ix_admission_pending', 'admission_requests', ['status', 'created_at'])


def downgrade():
    raise RuntimeError('Admission audit history must be retained.')
