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
from uuid import UUID, uuid5

from fastapi import HTTPException
from sqlalchemy import select

from workbench import intake
from workbench.db import intakes, planning_jobs, proposals, write_transaction
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


def process_one(engine, *, fail_before_commit=False, scenario_response=None):
    # Git inspection happens outside the writer transaction. Revalidate everything
    # before atomic publication; concurrent processors may inspect but cannot duplicate.
    with engine.connect() as c:
        row = c.execute(select(planning_jobs).join(intakes, planning_jobs.c.intake_id == intakes.c.id).where(
            planning_jobs.c.status == 'queued', intakes.c.payload['regression_tests'].as_string().is_not(None),
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
    contract = prepare(original['payload'], feedback, scenario_response) if job['kind'] == 'planning' else None
    if contract and len(notes) > 8:
        requirement = 'More than eight preparation revisions need an explicit consolidated request; older requirements must not be silently dropped.'
        contract['scope_missing'].append(requirement)
        contract['execution_blockers'].append(requirement)
    with write_transaction(engine) as c:
        current = intake.require(c, planning_jobs, job['id'])
        if current != job:
            return True
        fresh = intake.require(c, intakes, original['id'])
        try:
            if (fresh != original or intake.human_scope(c, original['id']) != scope
                    or job['payload'].get('scope_revision') != scope['revision']
                    or scope['stage'] != 'implementation'):
                raise ValueError('The human scope changed. Review it before preparing this test-only task.')
            if job['kind'] == 'planning':
                if job['revision'] <= original['revision']:
                    raise ValueError('A newer proposal supersedes this preparation job.')
                content = proposal_content(original['payload'], contract)
                identity = str(uuid5(UUID(original['id']), 'lua-test-preparation-' + str(job['revision'])))
                c.execute(proposals.insert().values(id=identity, intake_id=original['id'], revision=job['revision'],
                    scope_revision=scope['revision'], content=content, model=MODEL, basis='preparation', created_at=intake.now()))
                payload = {**job['payload'], 'prepared_proposal_id': identity, 'task_contract': contract,
                    'basis': 'preparation', 'evidence': [], 'result': dict(summary=content['summary'],
                        clarification=' '.join(contract['scope_missing'])[:800], evidence_ids=[], related_ids=[], proposal=content)}
                c.execute(intakes.update().where(intakes.c.id == original['id']).values(revision=job['revision'],
                    status='waiting_for_information' if contract['scope_missing'] else 'completed'))
            else:
                from workbench.planning_worker import materialize_single_task
                payload = {**job['payload'], 'decomposition': materialize_single_task(c, original, job, MODEL)}
            c.execute(planning_jobs.update().where(planning_jobs.c.id == job['id']).values(status='completed',
                payload=payload, model=MODEL, finished_at=intake.now(),
                message='Reusable Lua regression-test scope prepared. No model, run offer or execution permission created.'))
        except (ValueError, HTTPException) as error:
            c.execute(planning_jobs.update().where(planning_jobs.c.id == job['id']).values(status='failed',
                finished_at=intake.now(), message=str(error)[:800]))
            c.execute(intakes.update().where(intakes.c.id == original['id']).values(status='failed'))
        if fail_before_commit:
            raise RuntimeError('Simulated preparation crash before commit')
    return True
