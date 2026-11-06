"""Exact per-task routing choice, independent of the trusted verification profile."""
from sqlalchemy import select

from workbench import everyday, model_policy
from workbench.db import planning_jobs

CODEX_CHOICES = {'codex-once', 'codex-with-local-fallback'}
CHOICES = {'qwen-once', 'qwen-with-codex-fallback', *CODEX_CHOICES}
SAFE_CODEX_FALLBACK = {'authentication': 'unavailable', 'unavailable': 'unavailable', 'capacity': 'capacity'}


def codex_choice(saved):
    provider = saved.providers['codex']
    if not saved.revision or not provider.enabled or not provider.model:
        raise ValueError('Choose an enabled Codex model in Models.')
    return dict(policy_revision=saved.revision, model=provider.model, effort=provider.effort, fallback=[])


def configured_fallback(saved, backend, conditions):
    direction = 'qwen-to-codex' if backend == 'codex' else 'codex-to-qwen'
    permitted = (backend in saved.fallback if saved.test_fallback is None
                 else saved.test_fallback in {direction, 'both'})
    if saved.substitution != 'configured_fallback_only' or not permitted:
        raise ValueError('Enable the selected fallback provider in Models before approving it for a task.')
    allowed = sorted(set(saved.fallback_on) & conditions)
    if not allowed:
        raise ValueError('Enable a supported fallback condition in Models before approving it for a task.')
    return allowed


def planning_binding(c, choice):
    saved = model_policy.read_connection(c)
    if choice in CODEX_CHOICES:
        route = codex_choice(saved)
        if choice == 'codex-with-local-fallback':
            everyday.local_selection(c)
            route['fallback'] = configured_fallback(saved, 'local', set(SAFE_CODEX_FALLBACK.values()))
        return 'codex_route', route
    local = everyday.local_selection(c).model_dump()
    if choice == 'qwen-with-codex-fallback':
        local['codex'] = {k: v for k, v in codex_choice(saved).items() if k != 'policy_revision'}
        local['fallback'] = configured_fallback(saved, 'codex', {'unavailable'})
    return 'local_route', local


def approved(c, original):
    choice = original['payload'].get('local_planning')
    key, current = planning_binding(c, choice)
    receipts = c.execute(select(planning_jobs.c.payload).where(
        planning_jobs.c.intake_id == original['id'], planning_jobs.c.kind == 'planning')).scalars()
    for receipt in receipts:
        saved = receipt.get('local_planning_receipt', {}).get(key)
        if saved is not None and {k: v for k, v in saved.items() if k != 'policy_revision'} == {
                k: v for k, v in current.items() if k != 'policy_revision'}:
            return saved
    if choice == 'qwen-once':
        # Requests saved before per-provider consent retained the prior default binding.
        return model_policy.grounded_local_selection(c).model_dump()
    raise ValueError('The saved model policy changed after task consent. Review and submit a new request.')


def fallback_reason(choice, selected, error):
    from workbench.codex_provider import Hold
    from workbench.planning_provider import LocalBeforeGeneration

    if (choice == 'codex-with-local-fallback' and isinstance(error, Hold)
            and not error.started and SAFE_CODEX_FALLBACK.get(error.reason) in selected['fallback']):
        return error.reason
    if (choice == 'qwen-with-codex-fallback' and isinstance(error, LocalBeforeGeneration)
            and 'unavailable' in selected['fallback']):
        return error.reason
    return None


def run_selection(c, linked):
    choice = linked['original'].get('local_planning')
    if choice not in CHOICES:
        raise ValueError('Start an ordinary test request with an explicit provider choice.')
    from workbench import intake
    from workbench.db import intakes

    selected = approved(c, intake.require(c, intakes, linked['intake_id']))
    if choice == 'qwen-once':
        return None
    grounding = (linked['proposal_review'].get('task_contract') or {}).get('scenario_grounding') or {}
    basis = grounding.get('basis')
    reason = grounding.get('fallback_reason')
    if choice == 'qwen-with-codex-fallback':
        if basis == 'local' and not reason:
            return None
        if basis != 'codex' or reason != 'local_unreachable' or 'unavailable' not in selected['fallback']:
            raise ValueError('No eligible reviewed provider route for this task.')
        selected = {**selected['codex'], 'policy_revision': selected['policy_revision']}
    elif basis == 'local' and SAFE_CODEX_FALLBACK.get(reason) in selected['fallback']:
        return None  # The task spent its single pre-generation switch; keep Qwen for execution.
    elif basis != 'codex' or reason:
        raise ValueError('No eligible reviewed provider route for this task.')
    if grounding.get('model_selection') != {
            'model': selected['model'], 'policy_revision': selected['policy_revision']}:
        raise ValueError('The reviewed Codex selection changed.')
    return selected
