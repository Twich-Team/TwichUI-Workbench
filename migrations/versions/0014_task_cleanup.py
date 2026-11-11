"""Add organizational lifecycle and atomic cleanup receipts; retain original history."""
from alembic import op
import sqlalchemy as sa

revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None


def upgrade():
    # No FK to intakes: the ID/hash tombstone must survive safe permanent deletion.
    op.create_table('intake_lifecycle',
        sa.Column('intake_id', sa.String, primary_key=True),
        sa.Column('revision', sa.Integer, nullable=False),
        sa.Column('archived_at', sa.String),
        sa.Column('deleted_at', sa.String),
        sa.Column('original_sha256', sa.String, nullable=False))
    op.create_table('intake_cleanup_receipts',
        sa.Column('id', sa.String, primary_key=True),
        sa.Column('payload', sa.JSON, nullable=False),
        sa.Column('result', sa.JSON, nullable=False),
        sa.Column('created_at', sa.String, nullable=False))


def downgrade():
    c = op.get_bind()
    if c.execute(sa.text('SELECT count(*) FROM intake_lifecycle')).scalar() or c.execute(
            sa.text('SELECT count(*) FROM intake_cleanup_receipts')).scalar():
        raise RuntimeError('Cleanup history must be retained. Restore the matching recovery store instead.')
    op.drop_table('intake_cleanup_receipts')
    op.drop_table('intake_lifecycle')
