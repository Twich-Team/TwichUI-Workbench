"""Exact per-task routing choice, independent of the trusted verification profile."""
from sqlalchemy import select

from workbench import everyday, model_policy
from workbench.db import planning_jobs

CODEX_CHOICES = {'codex-once', 'codex-with-local-fallback'}
CHOICES = {'qwen-once', *CODEX_CHOICES}
SAFE_FALLBACK = {'authentication': 'unavailable', 'unavailable': 'unavailable', 'capacity': 'capacity'}


def codex_choice(c, choice):
    saved = model_policy.read_connection(c)
    provider = saved.providers['codex']
    if not saved.revision or not provider.enabled or not provider.model:
        raise ValueError('Choose an enabled Codex model in Models.')
    fallback = []
    if choice == 'codex-with-local-fallback':
        local = saved.providers['local']
        if (saved.substitution != 'configured_fallback_only' or 'local' not in saved.fallback
                or not local.enabled or local.model != everyday.MODEL or local.effort is not None):
            raise ValueError('Set local Qwen as an allowed fallback in Models before approving it for a task.')
        fallback = sorted(set(saved.fallback_on) & set(SAFE_FALLBACK.values()))
        if not fallback:
            raise ValueError('Choose provider unavailable or capacity exhausted as a fallback condition.')
    return dict(policy_revision=saved.revision, model=provider.model, effort=provider.effort, fallback=fallback)


def approved(c, original):
    choice = original['payload'].get('local_planning')
    codex = choice in CODEX_CHOICES
    current = codex_choice(c, choice) if codex else everyday.local_selection(c).model_dump()
    key = 'codex_route' if codex else 'local_route'
    receipts = c.execute(select(planning_jobs.c.payload).where(
        planning_jobs.c.intake_id == original['id'], planning_jobs.c.kind == 'planning')).scalars()
    for receipt in receipts:
        saved = receipt.get('local_planning_receipt', {}).get(key)
        if saved is not None and {k: v for k, v in saved.items() if k != 'policy_revision'} == {
                k: v for k, v in current.items() if k != 'policy_revision'}:
            return saved
    if not codex:
        # Requests saved before per-provider consent retained the prior default binding.
        from workbench.model_policy import grounded_local_selection

        return grounded_local_selection(c).model_dump()
    raise ValueError('The saved model policy changed after task consent. Review and submit a new request.')


def run_selection(c, linked):
    original = linked['original']
    if original.get('local_planning') not in CHOICES:
        raise ValueError('Start an ordinary test request with an explicit provider choice.')
    from workbench import intake
    from workbench.db import intakes

    selected = approved(c, intake.require(c, intakes, linked['intake_id']))
    if original['local_planning'] == 'qwen-once':
        return None
    grounding = (linked['proposal_review'].get('task_contract') or {}).get('scenario_grounding') or {}
    if grounding.get('basis') == 'codex':
        if grounding.get('model_selection', {}).get('model') != selected['model']:
            raise ValueError('The reviewed Codex selection changed.')
        return selected
    if grounding.get('basis') == 'local' and grounding.get('fallback_reason') in SAFE_FALLBACK:
        if SAFE_FALLBACK[grounding['fallback_reason']] in selected['fallback']:
            return None  # The task spent its single pre-generation switch; keep Qwen for execution.
    raise ValueError('No eligible reviewed provider route for this task.')
