"""Revisioned application display preferences; no worker policy ownership."""
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('display_settings', sa.Column('id', sa.Integer(), primary_key=True),
                    sa.Column('revision', sa.Integer(), nullable=False),
                    sa.Column('resource_hours', sa.Integer(), nullable=False),
                    sa.Column('refresh_seconds', sa.Integer(), nullable=False))


def downgrade():
    op.drop_table('display_settings')
