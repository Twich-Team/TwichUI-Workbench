"""Human scope owns the minimum safeguards. Planner content can only add to them."""
import hashlib
import json

from fastapi import HTTPException
from sqlalchemy import select

from workbench.db import intake_scopes, proposals, intakes, proposal_artifacts, proposal_decisions

RESEARCH_EXCLUSION = 'All addon implementation, execution permission and publication.'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def scope_history(connection, identity):
    rows = connection.execute(select(intake_scopes).where(intake_scopes.c.intake_id == identity)
                              .order_by(intake_scopes.c.revision)).mappings()
    return [{k:v for k,v in row.items() if k not in {'intake_id', 'command_id', 'change_payload'}} for row in rows]


def human_scope(connection, identity):
    history = scope_history(connection, identity)
    if not history:
        raise HTTPException(409, 'Human scope is missing. Scope review is required.')
    return history[-1]


def effective_requirements(scope, proposal, artifacts):
    """Pure shared domain rule; deliberately does not classify natural-language intent."""
    content = proposal['content']
    blockers, conflicts = [], []
    if scope['origin'] != 'human':
        blockers.append('Confirm the human interface scope for this existing request before approval.')
    if proposal['scope_revision'] != scope['revision']:
        blockers.append('Human scope changed. Open the current proposal revision.')
    human, model = scope['visual_requirement'], content['visual']
    if human == 'visual' and model == 'nonvisual':
        conflicts.append('You recorded an interface change; the planner called it nonvisual. Your visual requirement still applies.')
    if human == 'nonvisual' and model in {'visual', 'unknown'}:
        conflicts.append('You recorded no interface change; the planner found visual or uncertain impact. Both visual references are required for implementation review.')
    research = (scope['stage'] == 'research' and content['delivery'] == 'research'
                and RESEARCH_EXCLUSION in content['exclusions'])
    if not research and (scope['stage'] != 'implementation' or content['delivery'] != 'implementation'):
        conflicts.append('The human review stage and proposal delivery scope do not agree. Confirm the stage or request a revised proposal.')
        blockers.append('Research needs an explicit human research revision excluding implementation. Confirm the review stage.')
    required = [] if research or (human == 'nonvisual' and model == 'nonvisual') else ['mockup', 'game_context']
    roles = {a['role'] for a in artifacts if a['media_type'] == 'image/png'}
    for role, label in [('mockup', 'proposed mockup'), ('game_context', 'in-game-context reference')]:
        if role in required and role not in roles:
            blockers.append(f'Attach a {label} (PNG) for this revision.')
    return dict(required_artifacts=required, scope_conflicts=conflicts,
                approval_blockers=blockers, research_only=research)


def validate_review(connection, proposal, review, scope_revision, required_artifacts=None):
    """Used for API submission, application and decomposition (before and after work)."""
    row = connection.execute(select(intakes).where(intakes.c.id == proposal['intake_id'])).mappings().one()
    if (proposal['revision'] != row['revision'] or scope_revision != review['human_scope']['revision']
            or proposal['scope_revision'] != scope_revision):
        raise HTTPException(409, 'The proposal or human scope changed. Open the current revision before deciding.')
    if required_artifacts is not None and required_artifacts != review['required_artifacts']:
        raise HTTPException(409, 'The effective required artifact set changed. Review again.')
    if review['approval_blockers']:
        raise HTTPException(422, ' '.join(review['approval_blockers']))



def artifact_rows(connection, proposal_id):
    columns = [column for column in proposal_artifacts.c if column.name not in {'data', 'proposal_id'}]
    return [dict(r) for r in connection.execute(select(*columns).where(
        proposal_artifacts.c.proposal_id == proposal_id).order_by(proposal_artifacts.c.id)).mappings()]


def proposal_review(connection, proposal):
    artifacts = artifact_rows(connection, proposal['id'])
    scope = human_scope(connection, proposal['intake_id'])
    requirements = effective_requirements(scope, proposal, artifacts)
    from workbench.regression_preparation import contract_for
    prepared = contract_for(connection, proposal)
    if prepared:
        requirements['approval_blockers'].extend(prepared['scope_missing'])
    elif proposal['basis'] == 'preparation':
        requirements['approval_blockers'].append('This revised scope needs fresh regression-test preparation before approval.')
    bound = {'contract':2, 'proposal':dict(proposal), 'human_scope':scope,
             'required_artifacts':requirements['required_artifacts'], 'artifacts':artifacts}
    if prepared:
        bound['task_contract'] = prepared
    binding = digest(bound)
    return dict({k:v for k,v in proposal.items() if k != 'intake_id'}, artifacts=artifacts,
                binding=binding, human_scope=scope, **requirements,
                **({'task_contract':prepared} if prepared else {}))



def approval_needs_scope_review(connection, receipt):
    if receipt['scope_review_required']:
        return True
    if receipt['payload']['action'] != 'approve':
        return False
    if receipt['payload'].get('execution_continuation'):
        # Read-only projection uses the same canonical parent-scope validator;
        # a composite execution-budget binding is not a changed scope binding.
        from workbench.intake import validate_approval
        try:
            validate_approval(connection, receipt)
            return False
        except HTTPException:
            return True
    p = connection.execute(select(proposals).where(proposals.c.id == receipt['proposal_id'])).mappings().one()
    review = proposal_review(connection, dict(p))
    try:
        validate_review(connection, p, review, receipt['scope_revision'], receipt['required_artifacts'])
        from workbench.regression_evidence import validate_expectation_review
        validate_expectation_review(review, receipt['payload'])
    except HTTPException:
        return True
    return receipt['binding'] != review['binding'] or receipt['required_artifacts'] is None


def task_needs_scope_review(connection, item):
    if not item['proposal_id']:
        return False  # Issue triage is research, never implementation authority.
    approvals = connection.execute(select(proposal_decisions).where(
        proposal_decisions.c.proposal_id == item['proposal_id'],
        proposal_decisions.c.status == 'applied')).mappings().all()
    return not any(d['payload']['action'] == 'approve' and not approval_needs_scope_review(connection, d)
                   for d in approvals)
