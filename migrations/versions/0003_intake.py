"""Durable intake, revision-bound planning and backlog extensions."""
from alembic import op
from sqlalchemy import Column, ForeignKey, Integer, JSON, LargeBinary, MetaData, String, Table, UniqueConstraint
metadata = MetaData()

intakes = Table('intakes', metadata,
    Column('id', String, primary_key=True), Column('payload', JSON, nullable=False),
    Column('status', String, nullable=False), Column('revision', Integer, nullable=False),
    Column('created_at', String, nullable=False), Column('priority_override', Integer))
planning_jobs = Table('planning_jobs', metadata,
    Column('id', String, primary_key=True), Column('intake_id', ForeignKey('intakes.id'), nullable=False),
    Column('kind', String, nullable=False), Column('revision', Integer, nullable=False),
    Column('status', String, nullable=False), Column('attempts', Integer, nullable=False),
    Column('lease', String), Column('lease_until', String), Column('payload', JSON, nullable=False),
    Column('message', String, nullable=False), Column('model', String),
    Column('created_at', String, nullable=False), Column('finished_at', String),
    UniqueConstraint('intake_id', 'kind', 'revision', name='uq_planning_job'))
proposals = Table('proposals', metadata,
    Column('id', String, primary_key=True), Column('intake_id', ForeignKey('intakes.id'), nullable=False),
    Column('revision', Integer, nullable=False), Column('content', JSON, nullable=False),
    Column('model', String, nullable=False), Column('basis', String, nullable=False),
    Column('created_at', String, nullable=False),
    UniqueConstraint('intake_id', 'revision', name='uq_proposal_revision'))
proposal_artifacts = Table('proposal_artifacts', metadata,
    Column('id', String, primary_key=True), Column('proposal_id', ForeignKey('proposals.id'), nullable=False),
    Column('role', String, nullable=False), Column('label', String, nullable=False),
    Column('sha256', String, nullable=False), Column('media_type', String, nullable=False),
    Column('data', LargeBinary, nullable=False), Column('created_at', String, nullable=False))
proposal_decisions = Table('proposal_decisions', metadata,
    Column('id', String, primary_key=True), Column('intake_id', ForeignKey('intakes.id'), nullable=False),
    Column('proposal_id', ForeignKey('proposals.id'), nullable=False),
    Column('binding', String, nullable=False), Column('payload', JSON, nullable=False),
    Column('snapshot', JSON, nullable=False), Column('status', String, nullable=False),
    Column('message', String, nullable=False), Column('created_at', String, nullable=False),
    Column('finished_at', String),
    UniqueConstraint('proposal_id', 'binding', name='uq_proposal_decision'))
# Extension of the existing backlog: organizational/actionable local planning records.
backlog_items = Table('backlog_items', metadata,
    Column('id', String, primary_key=True), Column('intake_id', ForeignKey('intakes.id'), nullable=False),
    Column('proposal_id', ForeignKey('proposals.id')), Column('parent', ForeignKey('backlog_items.id')),
    Column('kind', String, nullable=False), Column('title', String, nullable=False),
    Column('purpose', String, nullable=False), Column('acceptance', JSON, nullable=False),
    Column('model', String, nullable=False), Column('created_at', String, nullable=False))
backlog_dependencies = Table('backlog_dependencies', metadata,
    Column('item_id', ForeignKey('backlog_items.id'), primary_key=True),
    Column('requires_id', ForeignKey('backlog_items.id'), primary_key=True))

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade():
    for table in (intakes, planning_jobs, proposals, proposal_artifacts, proposal_decisions,
                  backlog_items, backlog_dependencies):
        table.create(op.get_bind())


def downgrade():
    for table in (backlog_dependencies, backlog_items, proposal_decisions, proposal_artifacts,
                  proposals, planning_jobs, intakes):
        table.drop(op.get_bind())
