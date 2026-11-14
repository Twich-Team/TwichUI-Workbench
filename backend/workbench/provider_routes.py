"""Exact per-task routing choice, independent of the trusted verification profile."""
from sqlalchemy import select

from workbench import everyday, model_policy
from workbench.db import planning_jobs

CODEX_CHOICES = {'codex-once', 'codex-with-local-fallback'}
REMOTE_CHOICES = {*CODEX_CHOICES,'claude-once','smart-strategy'}
CHOICES = {'qwen-once', 'qwen-with-codex-fallback', *REMOTE_CHOICES}
SAFE_CODEX_FALLBACK = {'authentication': 'unavailable', 'unavailable': 'unavailable', 'capacity': 'capacity'}


def external_planner(selection, stop):
    if selection.get('strategy'):
        from workbench.smart_provider import Planner
    elif selection.get('provider')=='claude':
        from workbench.claude_client import Planner
    else:
        from workbench.codex_provider import Planner
    return Planner(selection,stop)


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


def planning_binding(c, choice, settings=None, *, apply_strategy=True):
    saved = model_policy.read_connection(c)
    if apply_strategy and saved.strategy and choice not in {'smart-strategy','qwen-once'}:
        from workbench.schemas import TaskAISettings,TaskModelChoice
        explicit=TaskAISettings.model_validate(settings) if settings else None
        if 'fallback' in choice or explicit and explicit.fallback:
            raise ValueError('Use the ordered Smart strategy to authorize fallback stages, or choose one model for this task.')
        backend='local' if choice=='qwen-once' else 'claude' if choice=='claude-once' else 'codex'
        expected=explicit.primary if explicit else TaskModelChoice(provider=backend,
            model=saved.providers[backend].model,effort=saved.providers[backend].effort)
        if expected.provider!=backend or not saved.providers[backend].enabled:
            raise ValueError('The explicit task choice does not match an enabled provider.')
        validate_selection(backend,expected.model,expected.effort)
        strategy=saved.strategy.model_copy(deep=True)
        stage=next((s for s in strategy.stages if s.provider==backend),None)
        override=bool(explicit and explicit.allocation_override)
        if stage is None:
            if not override:
                raise ValueError('This provider has no strategy allocation. Use the saved strategy or explicitly authorize an allocation override with Start now.')
            from workbench.ai_strategy import Stage,Strategy
            stage=Stage(id='manual',provider=backend,model=expected.model,effort=expected.effort,
                allocation=None if backend=='local' else 100,on_limit='wait')
            strategy=Strategy(stages=[stage])
        stage.model,stage.effort=expected.model,expected.effort
        route=dict(provider='smart',policy_revision=saved.revision,model=expected.model,effort=expected.effort,
            fallback=[],strategy=strategy.model_dump(),requested_stage=stage.id)
        if override:
            route['manual_stage']=stage.id
        return ('codex_route' if choice in REMOTE_CHOICES else 'local_route'),route
    if choice=='smart-strategy':
        from workbench.schemas import TaskAISettings
        from workbench.ai_strategy import Strategy
        frozen=TaskAISettings.model_validate(settings) if settings else None
        if not frozen or not frozen.strategy:
            raise ValueError('Review and freeze the strategy for this task before starting.')
        strategy=Strategy.model_validate(frozen.strategy)
        for stage in strategy.stages:
            if not saved.providers[stage.provider].enabled:
                raise ValueError('This strategy includes a disabled provider.')
            validate_selection(stage.provider,stage.model,stage.effort)
        first=strategy.stages[0]
        return 'codex_route',dict(provider='smart',policy_revision=saved.revision,
            model=first.model,effort=first.effort,fallback=[],strategy=strategy.model_dump())
    if settings:
        from workbench.schemas import TaskAISettings
        settings = TaskAISettings.model_validate(settings)
        expected = ('qwen' if settings.primary.provider == 'local' else settings.primary.provider) + (
            '-with-' + ('codex' if settings.fallback.provider == 'codex' else 'local') + '-fallback'
            if settings.fallback else '-once')
        if choice != expected:
            raise ValueError('The task model settings must match its provider consent.')
        providers = dict(saved.providers)
        for config in [settings.primary, *([settings.fallback] if settings.fallback else [])]:
            if not providers[config.provider].enabled:
                raise ValueError('This provider is disabled in Settings.')
            validate_selection(config.provider, config.model, config.effort)
            providers[config.provider] = providers[config.provider].model_copy(update={
                'model': config.model, 'effort': config.effort})
        # Task consent selects models, while global routing still limits fallback.
        saved = saved.model_copy(update={'providers': providers})
    if choice=='claude-once':
        config=saved.providers['claude']
        if not saved.revision or not config.enabled or not config.model or settings and settings.fallback:
            raise ValueError('Choose an enabled Claude model. Multi-provider Claude fallback requires a Smart AI strategy.')
        validate_selection('claude',config.model,config.effort)
        return 'codex_route',dict(provider='claude',policy_revision=saved.revision,
                                  model=config.model,effort=config.effort,fallback=[])
    if choice in CODEX_CHOICES:
        route = codex_choice(saved)
        if choice == 'codex-with-local-fallback':
            local_choice(saved)
            route['fallback'] = configured_fallback(saved, 'local', set(SAFE_CODEX_FALLBACK.values()))
            if settings:
                route['local'] = local_choice(saved)
        return 'codex_route', route
    local = local_choice(saved)
    if choice == 'qwen-with-codex-fallback':
        local['codex'] = {k: v for k, v in codex_choice(saved).items() if k != 'policy_revision'}
        local['fallback'] = configured_fallback(saved, 'codex', {'unavailable'})
    return 'local_route', local


def validate_selection(backend, model, effort):
    if backend == 'local':
        if model != everyday.MODEL or effort is not None:
            raise ValueError('Choose the installed Qwen model without reasoning effort.')
    elif model_policy.support(model_policy.capabilities(), backend, model, effort) != 'verified':
        raise ValueError('This model and effort need a current supported provider catalog. Refresh discovery before authorizing work.')


def local_choice(saved):
    config = saved.providers['local']
    if not saved.revision or not config.enabled:
        raise ValueError('Enable the installed local Qwen model in Settings.')
    validate_selection('local', config.model, config.effort)
    return dict(policy_revision=saved.revision, model=config.model)


def approved(c, original):
    choice = original['payload'].get('local_planning')
    from workbench.intake import digest
    manual=c.execute(select(planning_jobs.c.payload).where(planning_jobs.c.intake_id==original['id'],
        planning_jobs.c.kind.in_(['planning','decomposition']),
        planning_jobs.c.payload['manual_ai'].as_string().is_not(None))
        .order_by(planning_jobs.c.revision.desc(),planning_jobs.c.created_at.desc())).scalars().first()
    if manual:
        receipt=manual['manual_ai']
        if receipt['original_binding']!=digest(original['payload']) or receipt['scope_revision']!=manual.get('scope_revision'):
            raise ValueError('The explicit Start now choice no longer matches this task.')
        return receipt['route']
    from workbench.bug_authority import frozen, current
    if frozen(c, original['id']):
        return current(c, original['id'],admitting=False)['route']
    if original['payload'].get('ai_settings'):
        # The server receipt freezes this task's exact primary and fallback.
        # Global edits cannot change an already authorized operation.
        from workbench.intake import digest
        key = 'codex_route' if choice in REMOTE_CHOICES else 'local_route'
        receipts = c.execute(select(planning_jobs.c.payload).where(
            planning_jobs.c.intake_id == original['id'], planning_jobs.c.kind == 'planning')
            .order_by(planning_jobs.c.created_at)).scalars()
        for receipt in receipts:
            permission = receipt.get('local_planning_receipt', {})
            if key in permission and permission.get('binding') == digest(original['payload']):
                return permission[key]
        raise ValueError('The frozen task model receipt is unavailable.')
    key='codex_route' if choice in REMOTE_CHOICES else 'local_route'
    receipts=list(c.execute(select(planning_jobs.c.payload).where(
        planning_jobs.c.intake_id==original['id'],planning_jobs.c.kind=='planning')).scalars())
    saved_routes=[r['local_planning_receipt'][key] for r in receipts if key in r.get('local_planning_receipt',{})]
    for receipt in receipts:
        permission=receipt.get('local_planning_receipt',{})
        if permission.get('binding')==digest(original['payload']) and permission.get(key,{}).get('strategy'):
            return permission[key]
    _,current=planning_binding(c,choice,apply_strategy=False)
    for saved in saved_routes:
        if {k:v for k,v in saved.items() if k!='policy_revision'}=={k:v for k,v in current.items() if k!='policy_revision'}:
            return saved
    if choice == 'qwen-once' and not saved_routes:
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


def bug_followup_codex(route, followup):
    """The read-only reply keeps the settled producer and never enables fallback."""
    if route.get('strategy'):
        return route
    if followup['provider']=='claude' and route.get('provider')=='claude':
        return route
    if followup['provider'] == 'local':
        return None
    if followup['provider'] == 'codex' or followup['provider'] is None:
        if 'codex' in route:
            if followup['provider'] == 'codex':
                return {**route['codex'], 'policy_revision': route['policy_revision']}
        elif 'effort' in route:
            return route
        return None
    raise ValueError('The follow-up has no eligible provider route.')


def run_selection(c, linked):
    choice = linked['original'].get('local_planning')
    if choice not in CHOICES:
        raise ValueError('Start an eligible task with an explicit provider choice.')
    from workbench import intake
    from workbench.db import intakes

    selected = approved(c, intake.require(c, intakes, linked['intake_id']))
    if linked['approval']['payload'].get('feature_run'):
        from workbench.feature_steps import parent
        _, permission = parent(c, linked['approval']['id'])
        if permission['scope']['route'] != selected:
            raise ValueError('The feature saved model route changed.')
        if permission['scope']['provider'] == 'local':
            if selected['model'] != everyday.MODEL:
                raise ValueError('The installed local model differs from the feature permission.')
            return None
        return selected if selected.get('strategy') else {k:v for k,v in selected.items() if k in {'model','effort','policy_revision','provider'}}
    grounding = (linked['proposal_review'].get('task_contract') or {}).get('scenario_grounding') or {}
    basis = grounding.get('basis')
    reason = grounding.get('fallback_reason')
    if selected.get('strategy'):
        from workbench.ai_strategy import Strategy
        strategy=Strategy.model_validate(selected['strategy'])
        stage=next((s for s in strategy.stages if s.id==grounding.get('strategy_stage')),None)
        if (stage is None or basis!=stage.provider or grounding.get('model_selection')!={
                'model':stage.model,'policy_revision':selected['policy_revision']}):
            raise ValueError('The reviewed plan does not identify a permitted frozen strategy stage.')
        return None if stage.provider=='local' else selected
    if choice=='claude-once':
        if (basis!='claude' or reason or selected.get('provider')!='claude'
                or grounding.get('model_selection')!={'model':selected['model'],'policy_revision':selected['policy_revision']}):
            raise ValueError('The reviewed Claude selection changed.')
        return selected
    if choice == 'qwen-once' and basis in {None, 'demo'} and not reason:
        return None  # Legacy or disposable fixture; no Codex authority.
    if basis == 'local':
        allowed = ((choice in {'qwen-once', 'qwen-with-codex-fallback'} and not reason)
                   or (choice == 'codex-with-local-fallback'
                       and SAFE_CODEX_FALLBACK.get(reason) in selected['fallback']))
        if allowed and grounding.get('model_selection') == {
                'model': everyday.MODEL, 'policy_revision': selected['policy_revision']}:
            return None  # Keep the Qwen producer, including after a planning switch.
        raise ValueError('The reviewed Qwen selection changed.')
    if choice == 'qwen-with-codex-fallback':
        if basis != 'codex' or reason != 'local_unreachable' or 'unavailable' not in selected['fallback']:
            raise ValueError('No eligible reviewed provider route for this task.')
        selected = {**selected['codex'], 'policy_revision': selected['policy_revision']}
    elif choice not in CODEX_CHOICES or basis != 'codex' or reason:
        raise ValueError('No eligible reviewed provider route for this task.')
    if grounding.get('model_selection') != {
            'model': selected['model'], 'policy_revision': selected['policy_revision']}:
        raise ValueError('The reviewed Codex selection changed.')
    return selected
