"""Add explicit historical request ownership; original records remain unchanged."""
from alembic import op
import sqlalchemy as sa

revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('task_lineage',
        sa.Column('intake_id', sa.String, sa.ForeignKey('intakes.id'), primary_key=True),
        sa.Column('canonical_id', sa.String, sa.ForeignKey('intakes.id'), nullable=False),
        sa.Column('title', sa.String, nullable=False),
        sa.Column('relation', sa.String, nullable=False),
        sa.Column('evidence', sa.JSON, nullable=False),
        sa.Column('created_at', sa.String, nullable=False))
    op.create_index('ix_task_lineage_canonical', 'task_lineage', ['canonical_id'])


def downgrade():
    if op.get_bind().execute(sa.text('SELECT count(*) FROM task_lineage')).scalar():
        raise RuntimeError('Export and explicitly reverse reconciliation before removing its history.')
    op.drop_index('ix_task_lineage_canonical', table_name='task_lineage')
    op.drop_table('task_lineage')
