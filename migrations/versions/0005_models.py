"""Application-owned preferences and new attempt evidence; no historical backfill."""
from alembic import op
import sqlalchemy as sa

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('execution_policies',
        sa.Column('revision', sa.Integer, primary_key=True), sa.Column('payload', sa.JSON, nullable=False),
        sa.Column('saved_at', sa.String, nullable=False), sa.Column('actor', sa.String, nullable=False))
    op.create_table('model_attempts',
        sa.Column('id', sa.String, primary_key=True), sa.Column('job_id', sa.String, sa.ForeignKey('planning_jobs.id'), nullable=False),
        sa.Column('task_id', sa.String, nullable=False), sa.Column('source', sa.String, nullable=False),
        sa.Column('backend', sa.String), sa.Column('purpose', sa.String, nullable=False),
        sa.Column('requested_model', sa.String), sa.Column('actual_model', sa.String),
        sa.Column('requested_effort', sa.String), sa.Column('actual_effort', sa.String),
        sa.Column('policy_revision', sa.Integer), sa.Column('routing_reason', sa.String, nullable=False),
        sa.Column('started_at', sa.String, nullable=False), sa.Column('finished_at', sa.String),
        sa.Column('outcome', sa.String, nullable=False), sa.Column('verification', sa.String),
        sa.Column('usage', sa.JSON), sa.Column('account_ref', sa.String))


def downgrade():
    raise RuntimeError('Attempt and policy audit history must be retained. Restore a reviewed backup.')
