"""Human authority for visual scope; retain historical decisions and artifacts."""
from alembic import op
import sqlalchemy as sa

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('intake_scopes',
        sa.Column('intake_id', sa.String, sa.ForeignKey('intakes.id'), primary_key=True),
        sa.Column('revision', sa.Integer, primary_key=True),
        sa.Column('command_id', sa.String, nullable=False, unique=True),
        sa.Column('visual_requirement', sa.String, nullable=False),
        sa.Column('stage', sa.String, nullable=False), sa.Column('origin', sa.String, nullable=False),
        sa.Column('note', sa.String, nullable=False), sa.Column('change_payload', sa.JSON, nullable=False),
        sa.Column('created_at', sa.String, nullable=False))
    op.add_column('proposals', sa.Column('scope_revision', sa.Integer, nullable=False, server_default='0'))
    op.add_column('proposal_decisions', sa.Column('scope_revision', sa.Integer))
    op.add_column('proposal_decisions', sa.Column('required_artifacts', sa.JSON))
    op.add_column('proposal_decisions', sa.Column('scope_review_required', sa.Integer, nullable=False, server_default='1'))
    # Never infer a human classification from historical payload or model content.
    op.execute(sa.text("""INSERT INTO intake_scopes
        (intake_id,revision,command_id,visual_requirement,stage,origin,note,change_payload,created_at)
        SELECT id,0,'legacy-' || id,'unclassified','implementation','legacy',
        'No authoritative human scope was recorded. Confirm scope before approval.', '{}',created_at FROM intakes"""))
    op.execute(sa.text("""UPDATE intakes SET status='scope_review_required' WHERE id IN
        (SELECT intake_id FROM proposal_decisions WHERE status='applied'
         AND json_extract(payload,'$.action')='approve')"""))


def downgrade():
    raise RuntimeError('Human scope audit history cannot be discarded by downgrade. Restore a reviewed backup instead.')
