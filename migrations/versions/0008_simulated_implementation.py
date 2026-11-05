"""Atomic fixture-only attempts/candidates and explicit candidate verification."""
from alembic import op
import sqlalchemy as sa

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('implementation_requests',
        sa.Column('id', sa.String, primary_key=True),
        sa.Column('task_id', sa.String, sa.ForeignKey('backlog_items.id'), nullable=False),
        sa.Column('admission_id', sa.String, sa.ForeignKey('admission_requests.id'), nullable=False),
        sa.Column('binding', sa.String, nullable=False), sa.Column('payload', sa.JSON, nullable=False),
        sa.Column('snapshot', sa.JSON, nullable=False), sa.Column('status', sa.String, nullable=False),
        sa.Column('message', sa.String, nullable=False), sa.Column('created_at', sa.String, nullable=False),
        sa.Column('finished_at', sa.String),
        sa.UniqueConstraint('task_id', 'binding', name='uq_fixture_implementation'))
    op.create_index('ix_implementation_pending', 'implementation_requests', ['status', 'created_at'])
    op.create_table('implementation_attempts',
        sa.Column('id', sa.String, primary_key=True),
        sa.Column('request_id', sa.String, sa.ForeignKey('implementation_requests.id'), nullable=False, unique=True),
        sa.Column('outcome', sa.String, nullable=False), sa.Column('source', sa.String, nullable=False),
        sa.Column('started_at', sa.String, nullable=False), sa.Column('finished_at', sa.String, nullable=False))
    op.create_table('implementation_candidates',
        sa.Column('id', sa.String, primary_key=True),
        sa.Column('attempt_id', sa.String, sa.ForeignKey('implementation_attempts.id'), nullable=False, unique=True),
        sa.Column('provenance', sa.JSON, nullable=False), sa.Column('artifact', sa.LargeBinary, nullable=False),
        sa.Column('created_at', sa.String, nullable=False))
    op.create_table('candidate_verification_requests',
        sa.Column('id', sa.String, primary_key=True),
        sa.Column('task_id', sa.String, sa.ForeignKey('backlog_items.id'), nullable=False),
        sa.Column('candidate_id', sa.String, sa.ForeignKey('implementation_candidates.id'), nullable=False, unique=True),
        sa.Column('payload', sa.JSON, nullable=False), sa.Column('snapshot', sa.JSON, nullable=False),
        sa.Column('status', sa.String, nullable=False), sa.Column('verification', sa.String, nullable=False),
        sa.Column('checks', sa.JSON, nullable=False), sa.Column('message', sa.String, nullable=False),
        sa.Column('created_at', sa.String, nullable=False), sa.Column('started_at', sa.String),
        sa.Column('finished_at', sa.String))
    op.create_index('ix_candidate_verification_pending', 'candidate_verification_requests', ['status', 'created_at'])


def downgrade():
    raise RuntimeError('Simulated attempt, candidate and verification history must be retained.')
