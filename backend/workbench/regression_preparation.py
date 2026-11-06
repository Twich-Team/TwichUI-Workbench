"""Reusable test-only scope preparation. Read Git objects; never run repository code.

Preparation uses the existing intake/job/proposal/decision owners. It deliberately
does not register a runtime recipe, an offer, a budget or an execution permission.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import select as io_select
import subprocess
import time
from uuid import UUID, uuid5, uuid4

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from workbench import intake
from workbench.db import intakes, planning_jobs, proposals, model_attempts, write_transaction
from workbench.planning_transport import scenario_failure_message
from workbench.schemas import ProposalContent, RegressionTaskContract
from workbench.real_execution import LIMITS
from workbench.real_workspace import PRELUDE
from workbench.verification_profiles import select as select_profile

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
            ['git', '--no-optional-locks', '--no-replace-objects', '-c', 'protocol.allow=never', '-C', str(root), *args],
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


def repository():
    configured = os.environ.get('WORKBENCH_RESEARCH_ROOT')
    if not configured:
        raise RepositoryUnavailable('Configure an existing read-only TwichUI research checkout before resolving a base.')
    root = Path(configured)
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


def process_one(engine, *, fail_before_commit=False, scenario_response=None, adapter=None, stop_event=None):
    from workbench import everyday

    # Git inspection happens outside the writer transaction. Revalidate everything
    # before atomic publication; concurrent processors may inspect but cannot duplicate.
    with write_transaction(engine) as c:
        # A crashed scenario request is never automatically repeated. Reuse the
        # existing attempt ledger and leave an explicit retry/unknown outcome.
        running = c.execute(select(planning_jobs).join(intakes, planning_jobs.c.intake_id == intakes.c.id).where(
            planning_jobs.c.kind == 'planning', planning_jobs.c.status == 'running',
            intakes.c.payload['regression_tests'].as_string().is_not(None))).mappings().all()
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
        row = c.execute(select(planning_jobs).join(intakes, planning_jobs.c.intake_id == intakes.c.id).where(
            planning_jobs.c.status == 'queued', planning_jobs.c.kind.in_(['planning', 'decomposition']), intakes.c.payload['regression_tests'].as_string().is_not(None),
        ).order_by(planning_jobs.c.created_at, planning_jobs.c.id).limit(1)).mappings().first()
        if not row:
            return False
        job = dict(row)
        original = intake.require(c, intakes, job['intake_id'])
        scope = intake.human_scope(c, original['id'])
        # Preserve previous owner clarification across further revisions. These are
        # requirements to review, never interpreted operating instructions.
        notes = list(c.execute(select(planning_jobs.c.payload).where(
            planning_jobs.c.intake_id == original['id'], planning_jobs.c.kind == 'planning',
            planning_jobs.c.revision <= job['revision']).order_by(planning_jobs.c.revision.desc()).limit(9)).scalars())
    feedback = '\n'.join(dict.fromkeys(p['feedback'] for p in reversed(notes) if p.get('feedback')))
    adapter_used, failure, question, packet, model, basis = None, None, None, None, None, None
    model_selection = None
    claimed_generation = False
    loaded_selection = False
    codex_route = route = fallback_reason = None
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
                with engine.connect() as c:
                    everyday.check_planning(c, original)
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
            if not policy['scenario_generation']:
                raise ValueError('Preparation policy denies scenario generation for this task class.')
            if (job['payload'].get('scope_revision') != scope['revision'] or scope['stage'] != 'implementation'
                    or job['revision'] <= original['revision']):
                raise ValueError('Human scope or proposal changed before scenario generation; review the current request.')
            base_contract = prepare(original['payload'], feedback)
            if not base_contract['resolved_base']:
                raise ValueError('Workbench could not verify the configured source checkout for planning.')
            packet = grounded_scenarios.context(original['payload'], feedback, base_contract, policy)
            grounded_scenarios.require_plannable(packet)
            schema, wire = grounded_scenarios.planning_request(packet)
            loaded_selection = schema is grounded_scenarios.LoadedClientResponse
            if len(json.dumps(wire).encode()) > 24000:
                raise ValueError('Grounded scenario context exceeds the planning input budget; narrow the request.')
            if os.environ.get("WORKBENCH_DEMO_MANIFEST_SHA256"):
                from browser_demo.stage import require_active_stage
                require_active_stage()
            def provider_stopped():
                if stop_event is not None and stop_event.is_set():
                    return True
                try:
                    with engine.connect() as active:
                        return (intake.require(active, planning_jobs, job['id']) != job
                                or intake.human_scope(active, original['id']) != scope
                                or provider_routes.approved(active, original) != route)
                except (ValueError, OSError, HTTPException, OperationalError):
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
                    everyday.check_planning(c, original)
                    route = provider_routes.approved(c, original)
                    if choice in provider_routes.CODEX_CHOICES:
                        codex_route = route
                if codex_route:
                    model_selection = ScenarioModelSelection(policy_revision=route['policy_revision'], model=route['model'])
                    adapter_used = codex_provider.Planner(codex_route, provider_stopped)
                else:
                    adapter_used = adapter or (LocalProvider(stop_event) if everyday_mode else provider(stop_event))
                    if isinstance(adapter_used, LocalProvider):
                        model_selection = (ScenarioModelSelection(policy_revision=route['policy_revision'], model=route['model'])
                                           if route else model_policy.grounded_local_selection(c))
                lease = str(uuid4())
                job['payload'].pop('diagnostics', None)
                job['payload']['scenario_generation_claim'] = owner_identity()
                job.update(status='running', lease=lease, attempts=job['attempts']+1, message='Preparing grounded behavioral examples for independent review.')
                c.execute(planning_jobs.update().where(planning_jobs.c.id == job['id']).values(
                    status='running', lease=lease, payload=job['payload'], attempts=job['attempts'], message=job['message']))
                c.execute(model_attempts.insert().values(id=lease, job_id=job['id'], task_id=original['id'],
                    purpose='planning', source='workbench' if model_selection else 'unknown',
                    backend='codex' if codex_route else 'local' if model_selection else None,
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
            try:
                if codex_route or not model_selection:
                    raw, model, basis = adapter_used.generate(wire, schema)
                else:
                    raw, model, basis = adapter_used.generate(wire, schema, expected_model=model_selection.model)
            except PlanningUnavailable as error:
                reason = provider_routes.fallback_reason(choice, route, error) if route else None
                if not reason or provider_stopped():
                    raise
                if grounded_scenarios.context(original['payload'], feedback, prepare(original['payload'], feedback),
                        trusted_preparation.configuration(engine)) != packet:
                    raise ValueError('Committed source or preparation policy changed before fallback.')
                from datetime import datetime, timedelta, timezone
                with write_transaction(engine) as c:
                    if (intake.require(c, planning_jobs, job['id']) != job
                            or intake.require(c, intakes, original['id']) != original
                            or intake.human_scope(c, original['id']) != scope
                            or provider_routes.approved(c, original) != route
                            or (stop_event is not None and stop_event.is_set())):
                        raise ValueError('Approved task or provider policy changed before fallback.')
                    receipt = everyday.planning_receipt(original['payload'], original['created_at'])
                    ends = datetime.fromisoformat(receipt['expires_at'])
                    if datetime.now(timezone.utc) + timedelta(seconds=125) >= ends:
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
                    adapter_used = codex_provider.Planner(codex_route, provider_stopped)
                    raw, model, basis = adapter_used.generate(wire, schema)
                else:
                    adapter_used = LocalProvider(stop_event)
                    raw, model, basis = adapter_used.generate(wire, schema, expected_model=target)
            if len(json.dumps(raw, allow_nan=False).encode()) > 24000 or basis not in {'demo', 'local', 'codex'} or not isinstance(model, str) or not 1 <= len(model) <= 160:
                raise ValueError('Invalid bounded scenario provider response')
            parsed = grounded_scenarios.resolve_response(raw, schema, wire, packet)
            # Re-read committed evidence and private policy after the response.
            if grounded_scenarios.context(original['payload'], feedback, prepare(original['payload'], feedback),
                    trusted_preparation.configuration(engine)) != packet:
                raise ValueError('Committed source or preparation policy changed during generation; review fresh evidence.')
            scenario_response = parsed.model_dump(exclude={'evidence_sha256', 'policy_sha256'})
            scenario_response['model'] = model  # Actual adapter attribution, never a generated identity claim.
        except grounded_scenarios.ScenarioInputRequired as error:
            question = str(error)
        except ValidationError as error:
            if loaded_selection:
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
            failure = ('capacity_stop', hold) if hold else (
                (error.reason, str(error)) if isinstance(error, (codex_provider.Hold, LocalBeforeGeneration)) else (error.reason, None))
        except ValueError as error:
            failure = ('planning_validation', str(error))
        except OSError:
            failure = ('planning_io', 'Workbench could not read the files it needs for planning.')
    contract = prepare(original['payload'], feedback, scenario_response) if job['kind'] == 'planning' and not failure else None
    if generation and scenario_response is not None:
        contract['scenario_grounding'] = dict(evidence_sha256=packet['evidence_sha256'],
            policy_sha256=packet['policy_sha256'], basis=basis)
        if model_selection:
            contract['scenario_grounding']['model_selection'] = model_selection.model_dump()
        if fallback_reason:
            contract['scenario_grounding']['fallback_reason'] = fallback_reason
    if contract and question:
        from workbench.regression_evidence import expectation_digest
        prepared = contract['behavioral_evidence']
        prepared['holds'].insert(0, question)
        prepared['status'] = 'hold'
        prepared['review_digest'] = expectation_digest(prepared)
        contract['execution_blockers'].append(question)
        contract['scope_missing'].append(question)
    if generation and contract and contract['behavioral_evidence']['holds']:
        # New generated ambiguity needs clarification, not a scope-only approval
        # of guessed behavior. Historical deterministic scope reviews stay intact.
        if question or scenario_response['draft']['unresolved']:
            contract['scope_missing'] = list(dict.fromkeys([*contract['scope_missing'], *contract['behavioral_evidence']['holds']]))
        else:
            failure = ('planning_validation', contract['behavioral_evidence']['holds'][0])
    if contract and len(notes) > 8:
        requirement = 'More than eight preparation revisions need an explicit consolidated request; older requirements must not be silently dropped.'
        contract['scope_missing'].append(requirement)
        contract['execution_blockers'].append(requirement)
    message = 'Reusable Lua regression-test scope prepared. No model, run offer or execution permission created.'
    if generation and contract:
        message = ('Behavioral planning needs more information; no examples are ready for approval.'
                   if contract['scope_missing'] else
                   'Grounded behavioral proposal prepared for independent review; no certification or execution authority.')
    publication_failed = False
    with write_transaction(engine) as c:
        current = intake.require(c, planning_jobs, job['id'])
        if current != job:
            return True
        fresh = intake.require(c, intakes, original['id'])
        scope_changed = (any(fresh[field] != original[field] for field in ('revision', 'status', 'payload'))
                         or intake.human_scope(c, original['id']) != scope)
        if failure:
            category, detail = failure
            diagnostics = {k: v for k, v in getattr(adapter_used, 'diagnostics', {}).items()
                           if k != 'usage'}
            diagnostics['category'] = category
            if loaded_selection and claimed_generation:
                diagnostics['retained_failure'] = 1
            state = diagnostics.get('generation_state')
            if state == 'uncertain':
                detail = ('The provider returned some output, but completion is uncertain. Workbench held the task without switching.'
                          if diagnostics.get('partial_output') else
                          'Provider completion is uncertain. Workbench held the task without switching.')
            elif state == 'partial':
                detail = 'The provider finished with partial output. Workbench held the task without switching.'
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
                raise ValueError('The human scope changed. Review it before preparing this test-only task.')
            if job['kind'] == 'planning':
                if job['revision'] <= original['revision']:
                    raise ValueError('A newer proposal supersedes this preparation job.')
                content = proposal_content(original['payload'], contract)
                clarification = contract['scope_missing']
                if question:
                    clarification = clarification[:1]
                elif generation and (questions := scenario_response['draft']['unresolved']):
                    clarification = contract['behavioral_evidence']['holds'][:len(questions)]
                identity = str(uuid5(UUID(original['id']), 'lua-test-preparation-' + str(job['revision'])))
                c.execute(proposals.insert().values(id=identity, intake_id=original['id'], revision=job['revision'],
                    scope_revision=scope['revision'], content=content, model=model or MODEL, basis='preparation', created_at=intake.now()))
                payload = {**job['payload'], **({'scenario_generation': dict(model=model, basis=basis, evidence_sha256=packet['evidence_sha256'] if packet else None, policy_sha256=packet['policy_sha256'] if packet else None, diagnostics=getattr(adapter_used, 'diagnostics', {}))} if generation else {}), 'prepared_proposal_id': identity, 'task_contract': contract,
                    'basis': 'preparation', 'evidence': [], 'result': dict(summary=content['summary'],
                        clarification=' '.join(clarification)[:800], evidence_ids=[], related_ids=[], proposal=content)}
                c.execute(intakes.update().where(intakes.c.id == original['id']).values(revision=job['revision'],
                    status='waiting_for_information' if contract['scope_missing'] else 'completed'))
            else:
                from workbench.planning_worker import materialize_single_task
                payload = {**job['payload'], 'decomposition': materialize_single_task(c, original, job, MODEL)}
                # Durable preparation intent commits atomically with task identity.
                task_id = 'intake-' + original['id'] + '-' + str(job['revision']) + '-implementation'
                selected = intake.require(c, proposals, job['payload']['proposal_id'])
                prepared = (contract_for(c, selected) or {}).get('behavioral_evidence') or {}
                if prepared.get('status') == 'review_required' and not prepared['holds']:
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
