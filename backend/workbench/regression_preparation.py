"""Reusable test-only scope preparation. Read Git objects; never run repository code.

Preparation uses the existing intake/job/proposal/decision owners. It deliberately
does not register a runtime recipe, an offer, a budget or an execution permission.
"""

from workbench import worker_operation as operation

import copy
import hashlib
import json
import os
import re
import select as io_select
import subprocess
import time
from uuid import UUID, uuid5, uuid4

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import OperationalError

from workbench import intake
from workbench.bug_evidence import validation_diagnostics as bug_validation_diagnostics
from workbench.db import intakes, planning_jobs, proposals, model_attempts, write_transaction
from workbench.planning_transport import deadline_seconds, scenario_failure_message
from workbench.schemas import ProposalContent, RegressionTaskContract
from workbench.real_execution import LIMITS
from workbench.real_workspace import PRELUDE
from workbench.verification_profiles import select as select_profile
from workbench.visual_scope import digest

MODEL = 'deterministic-lua-test-preparation-v1'
TEST_PATH = re.compile(r'tests/test_[A-Za-z0-9_-]+\.lua')
ORACLE_REQUIREMENT = (
    'Before execution, independently certify a behavior-specific frozen oracle '
    'with an original/no-additions failure, a known-good passing addition, and '
    'negative controls (including a vacuous test and incorrect expected behavior). '
    'Passing the candidate-authored tests alone is insufficient.'
)


class RepositoryUnavailable(ValueError):
    pass


def git(root, *args, max_bytes=131072):
    """Fixed local read operations only. No ambient Git overrides or credentials."""
    process = None
    try:
        process = subprocess.Popen(
            ['git', '--no-optional-locks', '--no-replace-objects', '-c', 'protocol.allow=never',
             '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null',
             '-c', 'submodule.recurse=false', '-C', str(root), *args],
            env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8',
                 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
                 'GIT_TERMINAL_PROMPT': '0', 'GIT_NO_LAZY_FETCH': '1'},
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        raw = bytearray()
        deadline = time.monotonic() + 3
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not io_select.select([process.stdout], [], [], remaining)[0]:
                raise subprocess.TimeoutExpired('bounded Git inspection', 3)
            chunk = os.read(process.stdout.fileno(), min(65536, max_bytes + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
            if len(raw) > max_bytes:
                raise RepositoryUnavailable('Repository evidence exceeds the bounded preparation limit.')
        if process.wait(timeout=max(0.001, deadline - time.monotonic())):
            raise subprocess.CalledProcessError(process.returncode, 'bounded Git inspection')
        return bytes(raw)
    except (OSError, subprocess.SubprocessError):
        raise RepositoryUnavailable('The configured repository or committed base is unavailable. No repository code ran.') from None
    finally:
        if process:
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdout.close()


def repository(*, retained_base=None):
    configured = os.environ.get('WORKBENCH_RESEARCH_ROOT')
    if not configured:
        raise RepositoryUnavailable('Configure an existing read-only TwichUI research checkout before resolving a base.')
    from workbench import project_source
    try:
        root = project_source.retained_root(retained_base) if retained_base else project_source.active_root()
    except (OSError,ValueError):
        raise RepositoryUnavailable('The configured project checkout or retained source snapshot is unavailable. No repository code ran.') from None
    if (not root.is_absolute() or root.is_symlink() or root.resolve() != root
            or not root.is_dir() or root.stat().st_uid != os.getuid()
            or not (root / '.git').exists() or (root / '.git').is_symlink()):
        raise RepositoryUnavailable('The configured research checkout must be an owned repository without symlink traversal.')
    origin = git(root, 'config', '--get', 'remote.origin.url').decode().strip()
    if origin not in ('https://github.com/Twich-Team/TwichUI.git',
                      'https://github.com/Twich-Team/TwichUI',
                      'git@github.com:Twich-Team/TwichUI.git'):
        raise RepositoryUnavailable('The configured repository origin does not identify Twich-Team/TwichUI.')
    if git(root, 'rev-parse', '--show-toplevel').decode().strip() != str(root):
        raise RepositoryUnavailable('The research root must be the repository root.')
    base = git(root, 'rev-parse', '--verify', 'HEAD^{commit}').decode().strip()
    if not re.fullmatch(r'[a-f0-9]{40}', base):
        raise RepositoryUnavailable('A resolved SHA-1 commit is required by the existing Lua runtime.')
    entries = {}
    for record in git(root, 'ls-tree', '-z', base + ':tests', max_bytes=262144).split(b'\0'):
        if not record:
            continue
        metadata, name = record.split(b'\t', 1)
        mode, kind, oid = metadata.decode('ascii').split()
        name = name.decode('utf-8')
        if mode in ('100644', '100755') and kind == 'blob':
            entries['tests/' + name] = oid
    if len(entries) > 600:
        raise RepositoryUnavailable('The test inventory exceeds the 600-file preparation limit.')
    return root, base, entries


def blob(root, oid):
    if int(git(root, 'cat-file', '-s', oid)) > 131072:
        raise RepositoryUnavailable('A required test/framework file exceeds 128 KiB; bounded review is needed.')
    raw = git(root, 'cat-file', 'blob', oid)
    raw.decode('utf-8')  # Refuse ambiguous/binary evidence.
    return raw


def prepare(original, feedback='', scenario_response=None):
    from workbench import regression_evidence as behavioral
    profile = select_profile('lua-regression-tests')
    request = original['regression_tests']
    missing, evidence, commands, paths, candidates = [], [], [], [], []
    base = None
    draft, origin, response_hold, response = None, 'no-behavior-planner', None, None
    if scenario_response is not None:
        # Explicit internal structured response seam, never a provider activation.
        # Binding prevents a generated draft being borrowed by another revision.
        try:
            if len(json.dumps(scenario_response, allow_nan=False).encode()) > 24000:
                raise ValueError('Response budget')
            response = behavioral.ScenarioResponse.model_validate(scenario_response)
            draft = response.draft.model_dump(mode='json')
            origin = response.model
        except (TypeError, ValueError):
            response_hold = 'The behavioral draft is unsupported. Specify bounded fixture actions, a scalar expected observation and a behavior-relevant incorrect outcome to reject; arbitrary Lua or model assertions cannot establish the expectation.'
    text = original['title'] + '\n' + original['description'] + '\n' + feedback
    selected = request['test_paths'] or list(dict.fromkeys(
        'tests/' + name for name in re.findall(r'(?<![\w./!-])(?:tests/)?(test_[A-Za-z0-9_-]+\.lua)\b', text)))
    if len(selected) > 3:
        missing.append('Select at most three existing test files for one bounded regression task.')
    try:
        root, base, inventory = repository()
        # Candidate suggestions are names only, never inferred writable permission.
        words = set(re.findall(r'[a-z]{3,}', text.lower())) - {'test', 'tests', 'lua', 'add', 'the', 'and'}
        names = [p for p in inventory if TEST_PATH.fullmatch(p)]
        candidates = sorted(names, key=lambda p: (-len(words & set(p[11:-4].split('_'))), p))[:8]
        for path in dict.fromkeys(selected[:3]):
            if not TEST_PATH.fullmatch(path) or path not in inventory:
                missing.append('Select an existing regular test file in the resolved base: ' + path[:240])
            else:
                paths.append(path)
        if not selected:
            missing.append('Name an existing tests/test_*.lua file in the request or the test-path field; suggestions are not selected automatically.')
        if response and response.input_sha256 != behavioral.input_binding(original, feedback, base, paths):
            response_hold = 'The behavioral draft does not match this request, committed base, selected tests and feedback. Prepare fresh example inputs and expected outcomes.'
            draft, origin = None, 'stale-behavior-draft'
        source_paths = list(dict.fromkeys(p for s in (draft or {}).get('scenarios', []) for p in s['source_paths']))
        for path in ['tests/run.sh', 'tests/harness.lua', *paths, *source_paths]:
            oid = inventory.get(path)
            if path in source_paths:
                entry = git(root, 'ls-tree', base, '--', path).decode().strip().split()
                oid = entry[2] if len(entry) == 4 and entry[0] in ('100644', '100755') and entry[1] == 'blob' and entry[3] == path else None
            if not oid:
                missing.append('Required existing Lua framework file is missing: ' + path)
                continue
            raw = blob(root, oid)
            evidence.append(dict(path=path, git_blob=oid, sha256=hashlib.sha256(raw).hexdigest()))
            if path == 'tests/run.sh' and not all(token in raw.decode() for token in
                    ('for t in test_*.lua; do', 'lua5.1', 'require("bit")', 'PASSED', 'ERROR:')):
                missing.append('Inspect the changed test runner: compatibility with the existing Lua verification commands is unconfirmed.')
        if paths and not missing:
            commands = [dict(argv=['luac5.1', '-p', p], cwd='.', purpose='Syntax check') for p in paths]
            commands += [dict(argv=['lua5.1', '-e', PRELUDE, p.split('/')[-1]], cwd='tests',
                              purpose='Focused existing-framework regression test') for p in paths]
            commands.append(dict(argv=['bash', 'tests/run.sh'], cwd='.', purpose='Complete discovered suite; require exact inventory and zero failures'))
    except (UnicodeError, ValueError, OSError) as error:
        # Do not expose local filesystem paths or raw Git diagnostics to the client.
        # Preserve the known commit/evidence, but mark incomplete inspection honestly.
        missing.append(str(error) if isinstance(error, RepositoryUnavailable) else
                       'Repository evidence could not be inspected safely; review the configured checkout.')
    prepared_behavior = behavioral.plan(original, paths, draft, origin=origin, feedback=feedback, base=base)
    if prepared_behavior['status'] == 'review_required':
        try:
            behavioral.compile_plan(prepared_behavior)
        except ValueError as error:
            response_hold = str(error)
    if response_hold:
        prepared_behavior['holds'].append(response_hold)
        prepared_behavior['status'] = 'hold'
        prepared_behavior['review_digest'] = behavioral.expectation_digest(prepared_behavior)
    acceptance = list(request['acceptance'])
    if feedback:
        acceptance.append('Address the complete owner feedback retained in the proposed task contract; do not treat it as operating permission.')
    blockers = [*missing, *prepared_behavior['holds'], ORACLE_REQUIREMENT,
                'Revalidate the resolved base against destination main before execution and delivery. Local Git inspection does not establish remote freshness.',
                'A separate current human scope/run-budget decision and certified bounded runtime are required. Preparation creates no run offer or permission.']
    return RegressionTaskContract(
        behavioral_evidence=prepared_behavior,
        verification_profile=profile,
        resolved_base=base, intended_behavior=original['description'], acceptance=acceptance, review_feedback=feedback,
        writable_paths=paths, path_candidates=candidates, verification_commands=commands,
        independent_checks=[ORACLE_REQUIREMENT,
            'Independently enforce additive-only changes to the selected test files; reject deletions, harness/runner/production edits, symlinks and out-of-scope changes.',
            'Freeze base, patch, source/framework hashes, oracle and control results before the existing independent candidate checks; compare full-suite inventory against that base.',
            'Offline mocks do not establish live-game behavior. Require separate client evidence if acceptance depends on the real game.'],
        evidence=evidence, resource_limits={**LIMITS, 'overall_seconds': 5400, 'implementation_attempts': 1,
            'publication_seconds': 900, 'fallback_attempts': 0},
        scope_missing=missing, execution_blockers=blockers,
    ).model_dump(mode='json')


def contract_for(connection, proposal):
    if proposal['basis'] != 'preparation':
        return None
    payload = connection.execute(select(planning_jobs.c.payload).where(
        planning_jobs.c.intake_id == proposal['intake_id'], planning_jobs.c.kind == 'planning',
        planning_jobs.c.revision == proposal['revision'], planning_jobs.c.status == 'completed')).scalar()
    if not payload or payload.get('prepared_proposal_id') != proposal['id']:
        return None
    if payload.get('operation_kind') == 'scope_binding':
        from workbench.scope_binding import local_operation, prepared_job
        local_operation(connection, prepared_job(connection, proposal))
    return RegressionTaskContract.model_validate(payload['task_contract']).model_dump(mode='json')


def record_behavioral_outcome(engine, ref, binding, outcome):
    """Latest diagnostic in the existing planning owner; never changes the contract.

    Every attempt retains its immutable outcome file. This projection has no effect
    on scope, certification, offer eligibility, retries, grants or execution.
    """
    from workbench.task_execution import snapshot
    with write_transaction(engine) as c:
        linked = snapshot(c, **ref)
        if linked['proposal_review']['binding'] != binding:
            raise ValueError('Reviewed examples changed before recording preparation outcome')
        proposal = linked['proposal_review']
        row = c.execute(select(planning_jobs).where(planning_jobs.c.intake_id == ref['intake_id'],
            planning_jobs.c.kind == 'planning', planning_jobs.c.revision == proposal['revision'],
            planning_jobs.c.status == 'completed')).mappings().one()
        c.execute(planning_jobs.update().where(planning_jobs.c.id == row['id']).values(
            payload={**row['payload'], 'behavioral_preparation_outcome': {**outcome, 'review_binding': binding}}))


def proposal_content(original, contract):
    return ProposalContent(
        summary=original['title'], value='Add focused regression coverage for the requested existing behavior.',
        feasibility='The existing Lua test preparation path can propose scope. Runtime execution remains held until independent behavioral checks are certified.',
        uncertainty=contract['execution_blockers'][:12], evidence_ids=[],
        behavior=[original['description'][i:i+800] for i in range(0, len(original['description']), 800)],
        scope=['Add regression assertions only to ' + ', '.join(contract['writable_paths']) + '.'
               if contract['writable_paths'] else 'Resolve the missing test-file selection before approving scope.'],
        exclusions=['Production code, shared harness/runner edits, test deletions, execution activation and publication.'],
        acceptance=contract['acceptance'], dependencies=[], risks=['Tests authored by the implementer cannot independently establish their own adequacy.'],
        stages=['Review the proposed contract.', 'Independently certify behavioral oracle and controls.',
                'Separately authorize a bounded run using the existing execution and verification owners.'],
        visual='nonvisual', delivery='implementation',
    ).model_dump(mode='json')


@operation.isolated
def process_one(engine, *, fail_before_commit=False, scenario_response=None, adapter=None, stop_event=None):
    from workbench import bug_investigation, everyday

    prepared_intake = or_(
        intakes.c.payload['regression_tests'].as_string().is_not(None),
        and_(intakes.c.payload['kind'].as_string() == 'issue',
             intakes.c.payload['local_planning'].as_string().is_not(None),
             intakes.c.payload['supported_task'].as_string().is_(None)))
    # Git inspection happens outside the writer transaction. Revalidate everything
    # before atomic publication; concurrent processors may inspect but cannot duplicate.
    with write_transaction(engine) as c:
        # A crashed scenario request is never automatically repeated. Reuse the
        # existing attempt ledger and leave an explicit retry/unknown outcome.
        running = c.execute(select(planning_jobs).where(operation.eligible(c, planning_jobs.c.intake_id)).join(intakes, planning_jobs.c.intake_id == intakes.c.id).where(
            planning_jobs.c.kind == 'planning', planning_jobs.c.status == 'running',
            prepared_intake)).mappings().all()
        from workbench.real_execution import alive, owner_identity
        for active in running:
            owner = active['payload'].get('scenario_generation_claim')
            if owner and not alive(owner):
                c.execute(planning_jobs.update().where(planning_jobs.c.id == active['id']).values(
                    status='failed', finished_at=intake.now(), lease=None, lease_until=None,
                    payload={**active['payload'], 'diagnostics': {'category': 'interrupted_unknown'}},
                    message=scenario_failure_message('interrupted_unknown')))
                c.execute(model_attempts.update().where(model_attempts.c.id == active['lease']).values(
                    outcome='interrupted_unknown', finished_at=intake.now()))
                current = intake.require(c, intakes, active['intake_id'])
                if (current['revision'] < active['revision']
                        and intake.human_scope(c, current['id'])['revision'] == active['payload'].get('scope_revision')):
                    c.execute(intakes.update().where(intakes.c.id == current['id']).values(status='failed'))
                return True
        row = c.execute(select(planning_jobs).where(operation.eligible(c, planning_jobs.c.intake_id)).join(intakes, planning_jobs.c.intake_id == intakes.c.id).where(
            planning_jobs.c.status == 'queued', planning_jobs.c.kind.in_(['planning', 'decomposition']), prepared_intake,
            __import__('workbench.strategy_waiting',fromlist=['due']).due(),
        ).order_by(__import__('workbench.work_controls',fromlist=['priority_order']).priority_order(planning_jobs.c.intake_id),planning_jobs.c.created_at, planning_jobs.c.id).limit(1)).mappings().first()
        if not row:
            return False
        operation.selected(planning_jobs, row)
        job = dict(row)
        original = intake.require(c, intakes, job['intake_id'])
        scope = intake.human_scope(c, original['id'])
        # Preserve previous owner clarification across further revisions. These are
        # requirements to review, never interpreted operating instructions.
        notes = list(c.execute(select(planning_jobs.c.payload).where(
            planning_jobs.c.intake_id == original['id'], planning_jobs.c.kind == 'planning',
            planning_jobs.c.revision <= job['revision']).order_by(planning_jobs.c.revision.desc())).scalars())
    feedback = '\n'.join(dict.fromkeys(p['feedback'] for p in reversed(notes) if p.get('feedback')))
    adapter_used, failure, question, packet, model, basis = None, None, None, None, None, None
    model_selection = None
    claimed_generation = False
    loaded_selection = False
    codex_route = route = fallback_reason = None
    planning_end = None
    bug = bug_investigation.requested(original['payload'])
    bug_result, limitation = None, None
    validation_diagnostics = {}
    inspection_packet = None
    response_evidence = None
    choice = original['payload'].get('local_planning')
    everyday_mode = everyday.enabled()
    generation = job['kind'] == 'planning' and scenario_response is None and (
        everyday_mode or bool(os.environ.get('WORKBENCH_PREPARATION_CONFIG')))
    if generation:
        from workbench import codex_provider, grounded_scenarios, model_policy, provider_routes, trusted_preparation, planning_worker
        from workbench.planning_provider import LocalBeforeGeneration, LocalProvider, provider, PlanningUnavailable
        raw = None
        try:
            if everyday_mode:
                # Fresh project code is obtained by a fixed credential-free
                # download component, before model admission. Existing bound
                # continuations do not silently change their source revision.
                if not job['payload'].get('bug_followup') or job['payload'].get('bug_followup',{}).get('source_refresh'):
                    from workbench.project_source import ensure_current,registry
                    from workbench.db import real_execution_requests
                    with engine.connect() as active:
                        pending=active.execute(select(model_attempts.c.id).where(model_attempts.c.finished_at.is_(None))).first()
                        pending=pending or active.execute(select(real_execution_requests.c.id).where(
                            real_execution_requests.c.status.in_(['pending','running','checking','cancel_requested']))).first()
                    if pending and os.environ.get('WORKBENCH_RESEARCH_ROOT') and registry().exists():
                        return False  # Never change active source under owned execution.
                    if job['payload'].get('source_refresh_retry_at',0)>time.time():
                        return False
                    try:
                        ensure_current()
                    except (ValueError,OSError,subprocess.SubprocessError):
                        with write_transaction(engine) as waiting:
                            if intake.require(waiting,planning_jobs,job['id'])==job:
                                waiting.execute(planning_jobs.update().where(planning_jobs.c.id==job['id']).values(
                                    payload={**job['payload'],'source_refresh_retry_at':time.time()+60},
                                    message='Waiting for a verified project-source refresh. No model request has started.'))
                        return False
                with engine.connect() as c:
                    everyday.check_planning(c, original, job if bug or choice=='smart-strategy' else None)
                if choice == 'qwen-once' and adapter is None and os.environ.get('WORKBENCH_LOCAL_KEY'):
                    from workbench.planning_provider import local_slot
                    try:
                        with local_slot():
                            pass  # Admission observation only; the actual request obtains its own locks.
                    except BlockingIOError:
                        from workbench.local_model_priority import waiting_message
                        with write_transaction(engine) as c:
                            if intake.require(c, planning_jobs, job['id']) == job:
                                c.execute(planning_jobs.update().where(planning_jobs.c.id==job['id']).values(
                                    message=waiting_message()))
                        return False
                try:
                    everyday.require_room(everyday.configuration(engine))
                except everyday.CapacityHold as error:
                    # Capacity may return without another model approval. The
                    # original deadline and request still govern the claim.
                    with write_transaction(engine) as c:
                        if intake.require(c, planning_jobs, job['id']) == job and job['message'] != str(error):
                            c.execute(planning_jobs.update().where(planning_jobs.c.id == job['id']).values(message=str(error)))
                    return False
            policy = trusted_preparation.configuration(engine)
            category = bug_investigation.CATEGORY if bug else 'lua-regression-tests'
            if not policy['scenario_generation'] or category not in policy['allowed_classes']:
                raise ValueError('Preparation policy denies scenario generation for this task class.')
            if (job['payload'].get('scope_revision') != scope['revision'] or scope['stage'] != 'implementation'
                    or job['revision'] <= original['revision']):
                raise ValueError('Human scope or proposal changed before scenario generation; review the current request.')
            def inspect_source(policy):
                if bug:
                    continuation=job['payload'].get('bug_followup',{})
                    frozen=continuation.get('source_scope')
                    retained=continuation.get('retained_source_packet') or job['payload'].get('ai_retained_packet')
                    if retained and not frozen:
                        frozen=dict(base=retained['base'],files={f['path']:f['sha256'] for f in retained['files']})
                    packet=bug_investigation.context(original['payload'], '' if continuation else feedback, policy,
                        **({'legacy':True} if retained and not retained.get('source_contract') else {}))
                    if frozen and retained:
                        from workbench.bug_source_planning import verify_packet
                        verify_packet(packet,retained)
                        if retained.get('policy_sha256')!=packet['policy_sha256']:
                            raise ValueError('The retained source policy changed.')
                        packet=copy.deepcopy(retained)
                    if frozen and (packet['base']!=frozen['base'] or
                            {f['path']:f['sha256'] for f in packet['files']}!=frozen['files']):
                        raise ValueError('The continuation source scope changed; no new source was transmitted.')
                    return packet
                base_contract = prepare(original['payload'], feedback)
                if not base_contract['resolved_base']:
                    raise ValueError('Workbench could not verify the configured source checkout for planning.')
                return grounded_scenarios.context(original['payload'], feedback, base_contract, policy)

            packet = inspect_source(policy)
            if bug:
                if not packet['files']:
                    raise grounded_scenarios.ScenarioInputRequired(
                        'What action triggers the problem, and what should happen instead? '
                        'Include the name of the screen, action or message you see if you know it.')
                schema, wire = bug_investigation.planning_request(packet, original['payload'], feedback)
            else:
                grounded_scenarios.require_plannable(packet)
                schema, wire = grounded_scenarios.planning_request(packet)
            loaded_selection = schema is grounded_scenarios.LoadedClientResponse
            from workbench.planning_provider import BUG_INPUT, MAX_INPUT
            if len(json.dumps(wire).encode()) > (BUG_INPUT if bug else MAX_INPUT):
                raise ValueError('Grounded scenario context exceeds the planning input budget; narrow the request.')
            if os.environ.get("WORKBENCH_DEMO_MANIFEST_SHA256"):
                from browser_demo.stage import require_active_stage
                require_active_stage()
            def provider_stopped():
                if ((stop_event is not None and stop_event.is_set())
                        or (planning_end is not None and time.monotonic() >= planning_end)):
                    return True
                try:
                    with engine.connect() as active:
                        return (intake.require(active, planning_jobs, job['id']) != job
                                or intake.human_scope(active, original['id']) != scope
                                or provider_routes.approved(active, original) != route)
                except (ValueError, OSError, HTTPException, OperationalError):
                    return True

            if everyday_mode:
                from workbench import strategy_waiting
                with engine.connect() as current:
                    prospective=provider_routes.approved(current,original)
                route=prospective
                if prospective.get('strategy') and strategy_waiting.preflight(engine,job,prospective,provider_stopped,suitable_local=(not bug or __import__('workbench.investigation_strategy',fromlist=['suitable_local']).suitable_local(packet,original['payload']))):
                    return True
            # Same job/lease and accounting owners; duplicate workers never call an
            # adapter for an already claimed job. No automatic inference retry.
            with write_transaction(engine) as c:
                if (intake.require(c, planning_jobs, job['id']) != job
                        or intake.require(c, intakes, original['id']) != original
                        or intake.human_scope(c, original['id']) != scope
                        or (stop_event is not None and stop_event.is_set())):
                    return True
                from workbench.schemas import ScenarioModelSelection

                if everyday_mode:
                    everyday.check_planning(c, original, job if bug or choice=='smart-strategy' else None)
                    route = provider_routes.approved(c, original)
                    planning_end = time.monotonic() + __import__('workbench.strategy_waiting',fromlist=['seconds_remaining']).seconds_remaining(job,bug_investigation.INVESTIGATION_SECONDS if bug else deadline_seconds())
                    if bug and job['payload'].get('bug_followup'):
                        codex_route = provider_routes.bug_followup_codex(route, job['payload']['bug_followup'])
                        fallback_reason = job['payload']['bug_followup']['fallback_reason']
                    elif route.get('strategy') or choice in provider_routes.REMOTE_CHOICES:
                        codex_route = route
                if codex_route:
                    model_selection = ScenarioModelSelection(policy_revision=route['policy_revision'], model=route['model'])
                    adapter_used = provider_routes.external_planner(codex_route, provider_stopped)
                else:
                    adapter_used = adapter or (LocalProvider(stop_event, ends_at=planning_end)
                                               if everyday_mode else provider(stop_event))
                    if isinstance(adapter_used, LocalProvider):
                        model_selection = (ScenarioModelSelection(policy_revision=route['policy_revision'], model=route['model'])
                                           if route else model_policy.grounded_local_selection(c))
                lease = str(uuid4())
                job['payload'].pop('diagnostics', None)
                job['payload']['scenario_generation_claim'] = owner_identity()
                if bug:
                    from workbench.investigation_recovery import capabilities
                    job['payload']['recovery_capabilities'] = (capabilities(getattr(adapter_used,'backend','codex')) if isinstance(adapter_used,codex_provider.Planner) else dict(kind='local-message-investigation-v1',source_writes=False,execution_dispatch=False,external_action_tools=False))
                job.update(status='running', lease=lease, attempts=job['attempts']+1, message=('Investigating the bug and preparing a repair plan.' if bug else 'Preparing grounded behavioral examples for independent review.'))
                c.execute(planning_jobs.update().where(planning_jobs.c.id == job['id']).values(
                    status='running', lease=lease, payload=job['payload'], attempts=job['attempts'], message=job['message']))
                c.execute(model_attempts.insert().values(id=lease, job_id=job['id'], task_id=original['id'],
                    purpose='planning', source='workbench' if model_selection else 'unknown',
                    backend=codex_route.get('provider','codex') if codex_route else 'local' if model_selection else None,
                    outcome='running', started_at=intake.now(),
                    policy_revision=model_selection.policy_revision if model_selection else None,
                    requested_model=model_selection.model if model_selection else None,
                    requested_effort=codex_route['effort'] if codex_route else None,
                    routing_reason=('Explicit task consent and saved Codex model.' if codex_route else
                                    ('Saved local planning model; Codex allowed only if Qwen cannot connect before generation.'
                                     if choice == 'qwen-with-codex-fallback' else 'Saved local planning model; fixed decoding, no fallback.') if model_selection else
                                    'Controlled planning adapter; no saved model selection consumed.')))
                c.execute(intakes.update().where(intakes.c.id == original['id']).values(status='investigating'))
                original['status'] = 'investigating'
            claimed_generation = True
            phase_started=time.monotonic()
            if getattr(adapter_used,'strategy_control',False):
                from workbench.strategy_waiting import options
                adapter_used.bind_operation(engine,lease,**options(job,suitable_local=(not bug or __import__('workbench.investigation_strategy',fromlist=['suitable_local']).suitable_local(packet,original['payload']))))
            initial_source_packet = packet
            if bug and getattr(adapter_used, 'source_investigation', False):
                adapter_used.request_seconds = job['payload'].get('local_planning_receipt', {}).get(
                    'codex_request_deadline_seconds', codex_provider.SECONDS)
            from workbench.planning_limits import operation_request_bound
            from workbench.planning_response import create_evidence
            evidence_schema = schema
            if bug and packet.get('plan_review') in {'source-grounded-v2', 'source-grounded-lua-v1', 'source-grounded-intent-v1'} and (isinstance(adapter_used, LocalProvider) or getattr(adapter_used, 'source_investigation', False)):
                from workbench.bug_investigation_stage import InvestigationResponse
                evidence_schema = InvestigationResponse
            response_evidence = create_evidence(lease, evidence_schema, wire.get('coverage') if bug else None,
                                                wire.get('files') if bug else None)
            if response_evidence:
                adapter_used.response_evidence = response_evidence
            try:
                if bug and (isinstance(adapter_used, LocalProvider) or getattr(adapter_used, 'source_investigation', False)) and model_selection:
                    from workbench.bug_source_planning import generate as investigate
                    raw, model, basis, packet = investigate(adapter_used, packet, original['payload'],
                        feedback, provider_stopped, model_selection.model, engine=engine,
                        max_requests=__import__('workbench.strategy_waiting',fromlist=['remaining']).remaining(job,operation_request_bound(job['payload'])))
                    inspection_packet = packet
                elif codex_route or not model_selection:
                    raw, model, basis = adapter_used.generate(wire, schema)
                else:
                    raw, model, basis = adapter_used.generate(wire, schema, expected_model=model_selection.model)
            except PlanningUnavailable as error:
                reason = (provider_routes.fallback_reason(choice, route, error)
                          if route and not job['payload'].get('bug_followup') else None)
                if not reason or provider_stopped():
                    raise
                if inspect_source(trusted_preparation.configuration(engine)) != packet:
                    raise ValueError('Committed source or preparation policy changed before fallback.')
                from datetime import datetime, timedelta, timezone
                with write_transaction(engine) as c:
                    if time.monotonic() >= planning_end:
                        raise PlanningUnavailable('The shared planning deadline expired.', 'timeout')
                    if (intake.require(c, planning_jobs, job['id']) != job
                            or intake.require(c, intakes, original['id']) != original
                            or intake.human_scope(c, original['id']) != scope
                            or provider_routes.approved(c, original) != route
                            or (stop_event is not None and stop_event.is_set())):
                        raise ValueError('Approved task or provider policy changed before fallback.')
                    receipt = everyday.planning_receipt(original['payload'], original['created_at'])
                    ends = datetime.fromisoformat(receipt['expires_at'])
                    if datetime.now(timezone.utc) + timedelta(seconds=max(0, planning_end-time.monotonic())+5) >= ends:
                        raise ValueError('The original planning approval cannot fit the fallback.')
                    fallback_reason = reason
                    before = 'codex' if codex_route else 'local'
                    after = 'local' if codex_route else 'codex'
                    codex_route = None if codex_route else {**route['codex'], 'policy_revision': route['policy_revision']}
                    target = codex_route['model'] if codex_route else everyday.MODEL
                    label = 'Codex' if codex_route else 'Qwen'
                    job['payload']['provider_switch'] = dict(from_provider=before, to_provider=after,
                        reason=reason, **{f'{before}_generation_started': False})
                    c.execute(planning_jobs.update().where(planning_jobs.c.id == job['id']).values(payload=job['payload']))
                    c.execute(model_attempts.update().where(model_attempts.c.id == job['lease']).values(
                        backend=after, requested_model=target, requested_effort=codex_route['effort'] if codex_route else None,
                        routing_reason=f'{error} The approved {label} fallback took over the one planning request.'))
                model_selection = model_selection.model_copy(update={'model': target})
                if codex_route:
                    adapter_used = provider_routes.external_planner(codex_route, provider_stopped)
                    if bug:
                        adapter_used.request_seconds = job['payload'].get('local_planning_receipt', {}).get(
                            'codex_request_deadline_seconds', codex_provider.SECONDS)
                    adapter_used.response_evidence = response_evidence
                    if bug:
                        from workbench.bug_source_planning import generate as investigate
                        raw, model, basis, packet = investigate(adapter_used, packet, original['payload'],
                            feedback, provider_stopped, target, engine=engine,
                            max_requests=__import__('workbench.strategy_waiting',fromlist=['remaining']).remaining(job,operation_request_bound(job['payload'])))
                        inspection_packet = packet
                    else:
                        raw, model, basis = adapter_used.generate(wire, schema)
                else:
                    adapter_used = LocalProvider(stop_event, ends_at=planning_end)
                    adapter_used.response_evidence = response_evidence
                    if bug:
                        from workbench.bug_source_planning import generate as investigate
                        raw, model, basis, packet = investigate(adapter_used, packet, original['payload'],
                            feedback, provider_stopped, target, engine=engine,
                            max_requests=__import__('workbench.strategy_waiting',fromlist=['remaining']).remaining(job,operation_request_bound(job['payload'])))
                        inspection_packet = packet
                    else:
                        raw, model, basis = adapter_used.generate(wire, schema, expected_model=target)
            if planning_end is not None and time.monotonic() >= planning_end:
                raise PlanningUnavailable('The shared planning deadline expired.', 'timeout')
            if len(json.dumps(raw, allow_nan=False).encode()) > (40000 if bug else 24000) or basis not in {'demo', 'local', 'codex', 'claude'} or not isinstance(model, str) or not 1 <= len(model) <= 160:
                raise ValueError('Invalid bounded scenario provider response')
            normalization = []
            if bug:
                from workbench.planning_response import normalize_bug
                raw, normalization = normalize_bug(raw)
            if response_evidence:
                response_evidence.parsed(raw, getattr(adapter_used, 'diagnostics', {}), normalization)
                if isinstance(getattr(adapter_used, 'diagnostics', None), dict):
                    adapter_used.diagnostics['evidence_id'] = response_evidence.identifier
            # Re-read committed evidence and private policy after the response.
            if inspect_source(trusted_preparation.configuration(engine)) != initial_source_packet:
                raise ValueError('Committed source or preparation policy changed during generation; review fresh evidence.')
            if bug:
                from workbench.bug_source_planning import verify_packet
                verify_packet(initial_source_packet, packet)
                bug_result = bug_investigation.resolve(raw, packet, original['payload'], feedback)
            else:
                parsed = grounded_scenarios.resolve_response(raw, schema, wire, packet)
                scenario_response = parsed.model_dump(exclude={'evidence_sha256', 'policy_sha256'})
                scenario_response['model'] = model  # Actual adapter attribution, never a generated identity claim.
            if response_evidence:
                response_evidence.record('validated', generation_state=getattr(adapter_used, 'diagnostics', {}).get('generation_state'))
        except grounded_scenarios.ScenarioInputRequired as error:
            question = str(error)
        except ValidationError as error:
            if response_evidence:
                try:
                    response_evidence.rejected(error, raw)
                except OSError:
                    adapter_used.diagnostics['evidence_write_failed'] = 1
            if bug:
                validation_diagnostics = bug_validation_diagnostics(error)
                failure = ('schema_validation', 'The provider returned an incomplete or invalid bug investigation.')
            elif loaded_selection:
                # Provider-controlled keys and values must not reach the review UI or ledger.
                failure = ('loaded_client_schema', None)
            else:
                issues = error.errors(include_input=False, include_context=False)
                draft = raw.get('draft') if isinstance(raw, dict) else None
                branch = 'GroundedDraft' if isinstance(draft, dict) and draft.get('scenarios') else 'ClarificationDraft'
                issue = next((e for e in issues if e['loc'][:2] == ('draft', branch)), issues[0])
                location = '.'.join(map(str, issue['loc'])) or 'response'
                failure = ('schema_validation', f'Planning returned an invalid example at {location}: {issue["msg"]}')
        except PlanningUnavailable as error:
            hold = getattr(stop_event, 'reason', None)
            if hold:
                failure = ('capacity_stop', hold)
            elif planning_end is not None and time.monotonic() >= planning_end:
                failure = ('timeout', 'The shared planning deadline expired.')
            else:
                failure = (error.reason, str(error) if isinstance(error, (codex_provider.Hold, LocalBeforeGeneration)) else None)
        except ValueError as error:
            if response_evidence:
                try:
                    response_evidence.rejected(error, raw, phase='local-validation')
                except OSError:
                    adapter_used.diagnostics['evidence_write_failed'] = 1
            if bug and getattr(error,'path',None)==['action'] and str(error)=='Ambiguous investigation wire fields.':
                validation_diagnostics=dict(validation_errors=1,validation_locations='action:invalid_outcome')
                failure=('schema_validation','The provider returned conflicting or invalid investigation transport fields.')
            else:
                failure = ('planning_validation', str(error))
        except OSError:
            failure = ('planning_io', 'Workbench could not read the files it needs for planning.')
        finally:
            if response_evidence:
                response_evidence.close()
    contract, bug_content = None, None
    if job['kind'] == 'planning' and not failure:
        if bug:
            if not bug_result and question:
                bug_result = bug_investigation.resolve(dict(
                    finding='Workbench needs more detail to locate the reported behavior.',
                    plan=None, question=None if job['payload'].get('bug_followup') else question,
                    limitation=question if job['payload'].get('bug_followup') else None),
                    packet, original['payload'], feedback)
            if bug_result:
                contract, bug_content, question, limitation = bug_result
            else:
                failure = ('planning_validation', 'This installation cannot investigate this report.')
        else:
            contract = prepare(original['payload'], feedback, scenario_response)
    if generation and not failure and contract is not None and (scenario_response is not None or (bug_result and basis)):
        contract['scenario_grounding'] = dict(evidence_sha256=digest(packet['files']) if bug else packet['evidence_sha256'],
            policy_sha256=packet['policy_sha256'], basis=basis)
        if model_selection:
            if route and route.get('strategy'):
                model_selection=model_selection.model_copy(update={'model':model})
                contract['scenario_grounding']['strategy_stage']=getattr(adapter_used,'decision',None).stage_id
            contract['scenario_grounding']['model_selection'] = model_selection.model_dump()
        if fallback_reason:
            contract['scenario_grounding']['fallback_reason'] = fallback_reason
    if contract and question and not bug:
        from workbench.regression_evidence import expectation_digest
        prepared = contract['behavioral_evidence']
        prepared['holds'].insert(0, question)
        prepared['status'] = 'hold'
        prepared['review_digest'] = expectation_digest(prepared)
        contract['execution_blockers'].append(question)
        contract['scope_missing'].append(question)
    if generation and contract and not bug and contract['behavioral_evidence']['holds']:
        # New generated ambiguity needs clarification, not a scope-only approval
        # of guessed behavior. Historical deterministic scope reviews stay intact.
        if question or scenario_response['draft']['unresolved']:
            contract['scope_missing'] = list(dict.fromkeys([*contract['scope_missing'], *contract['behavioral_evidence']['holds']]))
        else:
            failure = ('planning_validation', contract['behavioral_evidence']['holds'][0])
    message = 'Reusable Lua regression-test scope prepared. No model, run offer or execution permission created.'
    if generation and contract:
        message = ('Behavioral planning needs more information; no examples are ready for approval.'
                   if contract['scope_missing'] else
                   'Grounded behavioral proposal prepared for independent review; no certification or execution authority.')
    if bug:
        message = ('Bug investigation needs your reply.' if question else
                   'Workbench could not propose an offline repair for this report.' if limitation else
                   'Bug investigation prepared a plan for review. Implementation requires a separate decision.')
    publication_failed = False
    with write_transaction(engine) as c:
        current = intake.require(c, planning_jobs, job['id'])
        if current != job:
            return True
        fresh = intake.require(c, intakes, original['id'])
        scope_changed = (any(fresh[field] != original[field] for field in ('revision', 'status', 'payload'))
                         or intake.human_scope(c, original['id']) != scope)
        if failure and failure[0]=='ai_wait' and not scope_changed:
            from workbench.strategy_waiting import pause
            wait=getattr(adapter_used,'diagnostics',{}).get('ai_wait')
            if wait and pause(c,job,original,adapter_used,wait,active_seconds=time.monotonic()-phase_started):
                return True
        if failure:
            category, detail = failure
            if category == 'context_budget':
                detail = 'Workbench held source preparation before sending this request because the required context did not fit.'
            diagnostics = {k: v for k, v in getattr(adapter_used, 'diagnostics', {}).items()
                           if k != 'usage'}
            diagnostics['category'] = category
            diagnostics.update(validation_diagnostics)
            if response_evidence:
                diagnostics['evidence_id'] = response_evidence.identifier
            retained_packet=getattr(adapter_used,'inspection_packet',None)
            if bug and retained_packet and packet:
                try:
                    from workbench.bug_source_planning import verify_packet
                    verify_packet(initial_source_packet,retained_packet)
                    job['payload']['bug_inspection_packet']=retained_packet
                except (ValueError,KeyError,TypeError):
                    diagnostics['retained_context_unavailable']=1
            if (loaded_selection or bug) and claimed_generation:
                diagnostics['retained_failure'] = 1
            state = diagnostics.get('generation_state')
            if state == 'uncertain':
                detail = ('The provider returned some output, but completion is uncertain. Workbench held the task without switching.'
                          if diagnostics.get('partial_output') else
                          'Provider completion is uncertain. Workbench held the task without switching.')
            elif state == 'partial':
                detail = 'The provider finished with partial output. Workbench held the task without switching.'
            if not claimed_generation and job['attempts'] == 0:
                # This is persisted by the pre-dispatch owner, before any
                # atomic model claim. Absence of a process is never used as proof.
                job['payload']['preflight_rejection'] = dict(version=1, provider_dispatch='not_started',
                    input_binding=digest({k:v for k,v in job['payload'].items() if k not in {'diagnostics','preflight_rejection'}}))
            c.execute(planning_jobs.update().where(planning_jobs.c.id == job['id']).values(
                status='failed', finished_at=intake.now(), lease=None, lease_until=None,
                payload={**job['payload'], 'diagnostics': diagnostics},
                message=(f'{detail} Your original request and any example are saved.' if detail and (codex_route or state in {'uncertain', 'partial'})
                         else scenario_failure_message(category, detail[:600] if detail else None))))
            if not scope_changed:
                c.execute(intakes.update().where(intakes.c.id == original['id']).values(status='failed'))
            if claimed_generation:
                outcome = ('superseded' if scope_changed else 'interrupted_unknown' if state == 'uncertain'
                           or (state is None and category in {'canceled', 'timeout'})
                           else 'incomplete_output' if state == 'partial'
                           else 'canceled' if category == 'canceled'
                           else 'unavailable' if category == 'local_unreachable' or (state == 'not_started'
                               and category in provider_routes.SAFE_CODEX_FALLBACK) else 'failed')
                planning_worker.finish_attempt(c, job, adapter_used, model, basis, outcome)
            if fail_before_commit:
                raise RuntimeError('Simulated preparation crash before commit')
            return True
        try:
            if (scope_changed
                    or job['payload'].get('scope_revision') != scope['revision']
                    or scope['stage'] != 'implementation'):
                raise ValueError('The human scope changed. Review it before preparing this task.')
            if job['kind'] == 'planning':
                if job['revision'] <= original['revision']:
                    raise ValueError('A newer proposal supersedes this preparation job.')
                content = bug_content if bug else proposal_content(original['payload'], contract)
                clarification = contract['scope_missing']
                if question:
                    clarification = clarification[:1]
                elif generation and not bug and (questions := scenario_response['draft']['unresolved']):
                    clarification = contract['behavioral_evidence']['holds'][:len(questions)]
                identity = str(uuid5(UUID(original['id']), ('lua-bug-investigation-' if bug else 'lua-test-preparation-') + str(job['revision'])))
                c.execute(proposals.insert().values(id=identity, intake_id=original['id'], revision=job['revision'],
                    scope_revision=scope['revision'], content=content, model=model or MODEL, basis='preparation', created_at=intake.now()))
                from workbench.evidence_preview import excerpt as evidence_excerpt
                references = []
                if bug and packet:
                    for index, item in enumerate(packet['files']):
                        references.append(dict(kind='addon', id=f'bug-source-{index+1}', label=item['path'],
                            sha256=item['sha256'], source_revision=packet['base'],
                            excerpt=evidence_excerpt(
                                '\n\n'.join(f"From line {part['line']}:\n{part['text']}" for part in item['excerpts']))))
                payload = {**job['payload'], **({'bug_inspection_packet': inspection_packet} if inspection_packet else {}), **({'scenario_generation': dict(model=model, basis=basis, evidence_sha256=(digest(packet['files']) if bug else packet['evidence_sha256']) if packet else None, policy_sha256=packet['policy_sha256'] if packet else None, diagnostics=getattr(adapter_used, 'diagnostics', {}))} if generation else {}), 'prepared_proposal_id': identity, 'task_contract': contract,
                    'basis': 'preparation', 'evidence': references, 'result': dict(summary=content['summary'],
                        clarification=('' if limitation else ' '.join(clarification)[:800]),
                        evidence_ids=[ref['id'] for ref in references if not contract.get('bug_intent') or ref['label'] in contract['bug_intent']['plan']['read_paths']][:12], related_ids=[], proposal=content)}
                c.execute(intakes.update().where(intakes.c.id == original['id']).values(revision=job['revision'],
                    status='failed' if limitation else 'waiting_for_information' if contract['scope_missing'] else 'completed'))
            else:
                from workbench.planning_worker import materialize_single_task
                payload = {**job['payload'], 'decomposition': materialize_single_task(c, original, job, MODEL)}
                # Durable preparation intent commits atomically with task identity.
                task_id = 'intake-' + original['id'] + '-' + str(job['revision']) + '-implementation'
                selected = intake.require(c, proposals, job['payload']['proposal_id'])
                reviewed_contract = contract_for(c, selected) or {}
                prepared = reviewed_contract.get('behavioral_evidence') or {}
                if reviewed_contract.get('bug_evidence') or reviewed_contract.get('bug_intent') or (prepared.get('status') == 'review_required' and not prepared['holds']):
                    intake.enqueue(c, original['id'], 'behavioral_preparation', job['revision'],
                        dict(ref=dict(intake_id=original['id'], task_id=task_id, approval_id=job['payload']['approval_id'])))
            c.execute(planning_jobs.update().where(planning_jobs.c.id == job['id']).values(status='completed',
                payload=payload, model=model or MODEL, finished_at=intake.now(), lease=None, lease_until=None,
                message=message))
        except (ValueError, HTTPException) as error:
            publication_failed = True
            c.execute(planning_jobs.update().where(planning_jobs.c.id == job['id']).values(status='failed',
                finished_at=intake.now(), message=str(error)[:800]))
            if not scope_changed:
                c.execute(intakes.update().where(intakes.c.id == original['id']).values(status='failed'))
        if claimed_generation:
            outcome = 'superseded' if scope_changed else 'failed' if publication_failed else 'completed'
            planning_worker.finish_attempt(c, job, adapter_used, model, basis, outcome)
        if fail_before_commit:
            raise RuntimeError('Simulated preparation crash before commit')
    return True
