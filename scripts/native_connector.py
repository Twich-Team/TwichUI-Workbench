"""Native execution at the existing authenticated private connector boundary."""
import json
from pathlib import Path
from uuid import UUID
import time

from sqlalchemy import select
from workbench import codex_provider as codex, native_agent, native_codex
from workbench import provider_admission as admission
from workbench.ai_strategy import Strategy
from workbench.db import engine_for, real_execution_requests, real_execution_grants
from workbench.visual_scope import digest
from strategy_connector import Boundary, codex_observation


def handle(connection, request, binary, credential, runtime, evidence_root, config_path, resolve):
    observing = request.get('operation') == 'native-codex-observe'
    if (set(request) != {'operation', 'request_id', 'fence', 'root', 'waiting_since'}
            or request['operation'] not in {'native-codex', 'native-codex-observe'}
            or str(UUID(request['request_id'])) != request['request_id']
            or not observing and str(UUID(request['fence'])) != request['fence']):
        raise ValueError('Exact native request identity required')
    engine = engine_for(Path(evidence_root).parent / 'workbench.sqlite3')
    try:
        with engine.connect() as c:
            row = c.execute(select(real_execution_requests).where(real_execution_requests.c.id == request['request_id'])).mappings().one()
            grant = c.execute(select(real_execution_grants).where(real_execution_grants.c.id == row['grant_id'])).mappings().one()
            if (row['status'] != ('pending' if observing else 'running') or row['fence'] != request['fence']
                    or not native_agent.native(grant['snapshot']) or not native_agent.execution.valid(grant)
                    or grant['binding'] != digest(grant['snapshot'])
                    or request['root'] != grant['snapshot']['recovery']['root']):
                raise ValueError('Native execution is not owned by this current grant and fence')
            native_agent.validate_authority(c, grant, ongoing=True)
        task = native_agent.Runtime(request['root'])
        if task.identity() != grant['snapshot']['runtime'] or Path(binary) != native_codex.boundary.CLI:
            raise ValueError('Native implementation, verifier or client identity changed')
        if not task.pristine_input():
            raise ValueError('Native input changed before provider dispatch; authorship cannot be established')
        strategy = Strategy.model_validate(task.recipe['strategy'])
        if (not isinstance(request['waiting_since'], dict)
                or set(request['waiting_since']) - {stage.id for stage in strategy.stages}
                or any(type(value) not in (int, float) or not 0 < value <= time.time()
                    for value in request['waiting_since'].values())
                or not observing and request['waiting_since'] != row['result'].get('native_waiting_since', {})):
            raise ValueError('Saved native allowance-wait identity changed')
        boundary = Boundary(connection, binary, credential, runtime, evidence_root, config_path, resolve)
        if observing:
            options = dict(suitable_local=False, reviewed_local=False, waiting_since=request['waiting_since'],
                failures={}, manual_stage=None, requested_stage=None)
            result = boundary.choose(options, strategy)
            connection.sendall(json.dumps(result).encode() + b'\n')
            return

        def dispatch(stage):
            import tempfile
            with tempfile.TemporaryDirectory(prefix='native-admit-', dir=runtime) as directory:
                hosts = boundary.hosts(directory, 'codex')
                with codex.app_server(codex.command(binary, credential, hosts=hosts),
                        boundary.stop, time.monotonic() + 30) as rpc:
                    codex.initialize(rpc)
                    codex.require_checks(codex.account_checks(rpc.call('account/read', {'refreshToken': False})), 'account/read')
                    codex.require_checks(codex.catalog_checks(rpc.call('model/list', {'includeHidden': False}),
                        stage.model, stage.effort), 'model/list')
                    observed = rpc.call('account/rateLimits/read', {})
                    codex.smart_allowance(observed)
                    # Re-evaluate frozen strategy immediately before native dispatch.
                    state = admission.read(boundary.root / 'allowances.json', {})
                    pool = admission.allowance_pool(state, strategy)
                    current = {key: dict(value) for key, value in pool.items()}
                    current[stage.id]['requests'] -= 1
                    current['@codex:' + stage.role]['requests'] -= 1
                    reserved = admission.read(boundary.root / (request['request_id'] + '.json'), {})
                    if reserved.get('decision', {}).get('unknown_usage'):
                        current[stage.id]['unknown_requests'] -= 1
                        current['@codex:' + stage.role]['unknown_requests'] -= 1
                    from workbench.ai_strategy import evaluate
                    decision = evaluate(strategy, [codex_observation(observed)],
                        [dict(stage_id=key, **value) for key, value in current.items()], now=time.time(),
                        waiting_since=request['waiting_since'])
                    if decision.state != 'selected' or decision.stage_id != stage.id:
                        raise codex.Hold('capacity', started=False)
            prompt = ('Investigate and repair this explicitly authorized task using native tools. '
                'Work alone. No delegation, network, publication, commits, or changes to artwork/production files. '
                'Only tests/test_reminder_gallery.py may survive candidate export. Ordinary temporary files may be used '
                'in the disposable workspace, but remove them before finishing. Put scratch and generated outputs in '
                + str(task.scratch) + '. Use python3 -B; python, pytest and rg may be unavailable. '
                'Keep the existing Gallery class unchanged; put new regression cases elsewhere in the same file. '
                'The final patch may add/delete at most 150 lines and must fit 64 KiB. '
                'You may inspect, edit, run tests and revise independently. No separate checking program is required '
                'before editing. Preserve the original image-quality and catalogue tests.\n\n'
                + task.recipe['instruction'])
            result = native_codex.execute(task.root, task.workspace, task.scratch, request['request_id'],
                stage.model, stage.effort, prompt, credential, stop=boundary.stop,
                watched={path: task.recipe['disclosure'][path]['sha256'] for path in task.paths})
            if not result['settled']:
                raise codex.Hold('unavailable', started=True)
            after = next((observation for observation in boundary.observations(strategy)
                if observation.provider == 'codex'), None)
            return result, after

        result = admission.run(boundary.root, request['request_id'], strategy,
            observations=lambda: boundary.observations(strategy), dispatch=dispatch, stop=boundary.stop,
            waiting_since=request['waiting_since'])
        connection.sendall(json.dumps(result, ensure_ascii=True, allow_nan=False).encode() + b'\n')
    finally:
        engine.dispose()
