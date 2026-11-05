"""Durable fixture verification commands; no live execution owner."""
from alembic import op
import sqlalchemy as sa
revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('verification_requests',
        sa.Column('id', sa.String, primary_key=True),
        sa.Column('task_id', sa.String, sa.ForeignKey('tasks.id'), nullable=False),
        sa.Column('version', sa.String, nullable=False), sa.Column('payload', sa.JSON, nullable=False),
        sa.Column('snapshot', sa.JSON, nullable=False), sa.Column('status', sa.String, nullable=False),
        sa.Column('attempt_outcome', sa.String, nullable=False), sa.Column('verification', sa.String, nullable=False),
        sa.Column('checks', sa.JSON, nullable=False), sa.Column('message', sa.String, nullable=False),
        sa.Column('created_at', sa.String, nullable=False), sa.Column('started_at', sa.String), sa.Column('finished_at', sa.String),
        sa.UniqueConstraint('task_id', 'version', name='uq_fixture_verification'))


def downgrade():
    raise RuntimeError('Verification audit history must be retained.')
