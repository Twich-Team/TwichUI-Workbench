"""Explicit real-task permission and single-attempt ownership; never seed approvals."""
from alembic import op
import sqlalchemy as sa

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('real_execution_grants',
        sa.Column('id', sa.String, primary_key=True),
        sa.Column('snapshot', sa.JSON, nullable=False),
        sa.Column('binding', sa.String, nullable=False, unique=True),
        sa.Column('issued_at', sa.String, nullable=False),
        sa.Column('expires_at', sa.String, nullable=False), sa.Column('revoked_at', sa.String))
    op.create_table('real_execution_requests',
        sa.Column('id', sa.String, primary_key=True),
        sa.Column('grant_id', sa.String, sa.ForeignKey('real_execution_grants.id'), nullable=False, unique=True),
        sa.Column('payload', sa.JSON, nullable=False), sa.Column('status', sa.String, nullable=False),
        sa.Column('fence', sa.String), sa.Column('message', sa.String, nullable=False),
        sa.Column('created_at', sa.String, nullable=False), sa.Column('started_at', sa.String),
        sa.Column('finished_at', sa.String), sa.Column('result', sa.JSON))
    with op.batch_alter_table('model_attempts') as batch:
        batch.alter_column('job_id', existing_type=sa.String, nullable=True)
        batch.add_column(sa.Column('execution_id', sa.String))
        batch.create_foreign_key('fk_attempt_execution', 'real_execution_requests', ['execution_id'], ['id'])
        batch.create_unique_constraint('uq_attempt_execution', ['execution_id'])


def downgrade():
    raise RuntimeError('Real grants and consumed attempts must be preserved.')
