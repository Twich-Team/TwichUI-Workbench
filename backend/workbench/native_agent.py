"""Opt-in native-agent-v1 adapter for existing grants, requests and handoffs.

Trusted owner ingress freezes a NEW task. Missing engine_version remains legacy;
historical approvals are never migrated. The ordinary real_execution worker owns
claims, fencing, cancellation, terminal state and the existing model ledger.
"""
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import subprocess
import tempfile
import time
from uuid import UUID, uuid5

from sqlalchemy import select
from workbench import native_boundary as boundary, real_execution as execution
from workbench.db import intakes, intake_scopes, backlog_items, real_execution_grants, real_execution_requests, write_transaction
from workbench.real_workspace import Runtime as LegacyRuntime, durable, sha, tree

ENGINE = 'native-agent-v1'
VERIFIER = Path(__file__).with_name('native_gallery_verifier.py')
FOCUSED = 'tests/test_reminder_gallery.py'
HELPERS = ('native_agent.py', 'native_boundary.py', 'native_codex.py', 'native_gallery_verifier.py',
    'real_execution.py', 'real_handoff.py', 'real_publication.py', 'publication_adapter.py',
    'provider_admission.py', 'ai_strategy.py', 'codex_provider.py')


def native(snapshot):
    return snapshot.get('contract', {}).get('engine_version') == ENGINE


def safe_path(path):
    parts = PurePosixPath(path).parts
    return (isinstance(path, str) and 0 < len(path.encode()) <= 240 and parts
        and not PurePosixPath(path).is_absolute() and all(p not in {'.', '..'} for p in parts)
        and all(not p.startswith('.') and p not in {'AGENTS.md', 'SKILL.md'} for p in parts)
        and str(PurePosixPath(path)) == path and '\\' not in path and '\x00' not in path)


def original_payload(spec):
    first = spec['strategy']['stages'][0]
    return dict(kind='issue', request_id=spec['task_id'], title=spec['title'],
        description=spec['instruction'], lua_error='', visual_requirement='nonvisual',
        local_planning='smart-strategy', native_task=spec,
        ai_settings=dict(primary={key: first.get(key) for key in ('provider', 'model', 'effort')},
            fallback=None, strategy=spec['strategy']))


def validate_spec(spec):
    from workbench.ai_strategy import Strategy
    contract = spec['contract']
    if (contract.get('engine_version') != ENGINE or contract.get('repository') != 'Twich-Team/TwichUI'
            or contract.get('provider') != 'codex' or contract.get('paths') != [FOCUSED]
            or contract.get('operations') != {FOCUSED: ['modify']}
            or not re.fullmatch('[a-f0-9]{40}', contract.get('base', ''))
            or not Path(contract['source']).is_absolute() or spec.get('base_branch') != 'main'
            or spec.get('draft') is not True or spec.get('verification_profile') != 'native-gallery-v1'
            or contract.get('limits') != dict(implementation_s=900, acceptance_s=1200,
                memory_bytes=1 << 30, pids=128, cpu_percent=100, output_bytes=4 << 20,
                changed_lines=150, patch_bytes=65536)
            or not 1 <= len(spec['disclosure']) <= 512 or FOCUSED not in spec['disclosure']
            or not all(safe_path(p) for p in spec['disclosure'])
            or not 1 <= len(spec['instruction'].encode()) <= 32768
            or not re.fullmatch('[a-f0-9]{64}', spec['owner_instruction_sha256'])
            or not 1 <= len(spec['title']) <= 150
            or not isinstance(spec['identity'], dict) or set(spec['identity']) != {'name', 'email'}
            or any(not isinstance(v, str) or not 1 <= len(v) <= 160 or any(ord(c) < 32 for c in v)
                for v in spec['identity'].values())):
        raise ValueError('Unsupported frozen native task contract')
    UUID(spec['task_id'])
    if spec.get('historical_intake_id'):
        UUID(spec['historical_intake_id'])
    strategy = Strategy.model_validate(spec['strategy'])
    if contract.get('model') != strategy.stages[0].model:
        raise ValueError('Native contract and frozen primary selection differ')
    if any(stage.provider != 'codex' for stage in strategy.stages):
        raise ValueError('Only native Codex is qualified')
    for path, row in spec['disclosure'].items():
        if (set(row) != {'oid', 'sha256', 'mode'} or row['mode'] not in ('100644', '100755')
                or not re.fullmatch('[a-f0-9]{40}', row['oid'])
                or not re.fullmatch('[a-f0-9]{64}', row['sha256'])):
            raise ValueError('Exact source disclosure inventory required')
    return spec


def git(source, *args):
    return subprocess.check_output(['git', '--no-replace-objects', '-c', 'core.hooksPath=/dev/null',
        '-c', 'core.fsmonitor=false', '-c', 'protocol.allow=never', '-C', str(source), *args],
        env={'PATH': '/usr/bin:/bin', 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_TERMINAL_PROMPT': '0', 'GIT_NO_LAZY_FETCH': '1'}, timeout=30)


def inventory(source, base):
    result = {}
    for line in git(source, 'ls-tree', '-rz', base).split(b'\0'):
        if not line:
            continue
        meta, raw_path = line.split(b'\t', 1)
        mode, kind, oid = meta.decode().split()
        path = raw_path.decode()
        if mode not in ('100644', '100755') or kind != 'blob':
            raise ValueError('Native proof requires an entirely regular committed repository')
        result[path] = dict(oid=oid, mode=mode)
    if not result or len(result) > 4096:
        raise ValueError('Bounded committed source inventory required')
    return result


def materialize(source, base, destination, selected=None):
    entries = inventory(source, base)
    if selected is not None and not set(selected) <= entries.keys():
        raise ValueError('Source disclosure differs from committed source')
    destination.mkdir(mode=0o700)
    manifest = {}
    total = 0
    for path, entry in sorted(entries.items()):
        if selected is not None and path not in selected:
            continue
        # Repository control files are retained for independent regression only;
        # they never enter the native source disclosure inventory.
        parts = PurePosixPath(path).parts
        if PurePosixPath(path).is_absolute() or '..' in parts or '\\' in path:
            raise ValueError('Unsafe committed path')
        data = git(source, 'cat-file', 'blob', entry['oid'])
        total += len(data)
        if len(data) > 4 << 20 or total > (32 << 20 if selected is not None else 512 << 20):
            raise ValueError('Committed source exceeds frozen storage allowance')
        manifest[path] = dict(**entry, sha256=sha(data))
        if selected is not None and manifest[path] != selected[path]:
            raise ValueError('Disclosed Git blob identity changed')
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(0o755 if entry['mode'] == '100755' else 0o644)
    return manifest


def validate_authority(c, grant, *, ongoing=False):
    from workbench.task_execution import Waiting
    from workbench.work_controls import task_paused
    snapshot = grant['snapshot']
    try:
        spec = validate_spec(snapshot['native_task'])
        row = c.execute(select(intakes).where(intakes.c.id == spec['task_id'])).mappings().one()
        if (row['payload'] != original_payload(spec) or snapshot['contract'] != spec['contract']
                or snapshot['authorization_sha256'] != spec['owner_instruction_sha256']
                or not ongoing and task_paused(c, spec['task_id'])):
            raise ValueError('Native owner task changed or paused')
    except (ValueError, KeyError, TypeError):
        raise Waiting('The new native task or frozen owner permission changed.') from None


def prepare(root, spec):
    """Trusted preparation only; no model, execution grant or provider credential."""
    spec = validate_spec(spec)
    root = Path(root)
    root.mkdir(mode=0o700)
    durable(root / 'native-task.json', json.dumps(spec, sort_keys=True).encode())
    source, base = spec['contract']['source'], spec['contract']['base']
    original = inventory(source, base)
    for path, entry in original.items():
        entry['sha256'] = sha(git(source, 'cat-file', 'blob', entry['oid']))
    durable(root / 'base-manifest.json', json.dumps(original, sort_keys=True).encode())
    parent = Path(tempfile.mkdtemp(prefix='workbench-native-workspace-', dir='/dev/shm'))
    parent.chmod(0o700)
    workspace, scratch = parent / 'source', parent / 'scratch'
    scratch.mkdir(mode=0o700)
    materialize(source, base, workspace, spec['disclosure'])
    for args in (['init', '-q'], ['add', '.'], ['-c', 'user.name=Workbench', '-c',
            'user.email=workbench@local.invalid', 'commit', '-qm', 'Frozen authorized native source']):
        git(workspace, *args)
    control = parent / 'profile'
    control.mkdir(mode=0o700)
    boundary.profile(control, [], [workspace, scratch])
    baseline, _ = boundary.scan(workspace)
    durable(root / 'native-baseline.json', json.dumps(baseline, sort_keys=True).encode())
    durable(root / 'workspace-location.json', json.dumps(dict(path=str(workspace)), sort_keys=True).encode())
    return Runtime(root)


def register(engine, root, owner_instruction, provenance):
    """Explicit new-task owner ingress. Never exposed to model or HTTP output."""
    runtime = Runtime(root)
    spec = runtime.recipe
    if sha(owner_instruction.encode()) != spec['owner_instruction_sha256']:
        raise ValueError('Exact reviewed owner instruction required')
    payload = original_payload(spec)
    with write_transaction(engine) as c:
        prior = c.execute(select(intakes).where(intakes.c.id == spec['task_id'])).mappings().first()
        if prior:
            if prior['payload'] != payload:
                raise ValueError('Native task identity already has different frozen input')
        else:
            if spec.get('historical_intake_id') and not c.scalar(select(intakes.c.id).where(intakes.c.id == spec['historical_intake_id'])):
                raise ValueError('Historical report link is unavailable')
            c.execute(intakes.insert().values(id=spec['task_id'], payload=payload,
                status='tasks_ready', revision=1, created_at=execution.now()))
            c.execute(intake_scopes.insert().values(intake_id=spec['task_id'], revision=1,
                command_id=str(uuid5(UUID(spec['task_id']), 'native-owner-scope')),
                visual_requirement='nonvisual', stage='implementation', origin='human',
                note='The explicit owner instruction authorizes this new native task; no legacy approval was rewritten.',
                change_payload=dict(kind='trusted-native-owner-ingress',
                    owner_instruction_sha256=spec['owner_instruction_sha256'], provenance=provenance),
                created_at=execution.now()))
        task = c.execute(select(backlog_items).where(backlog_items.c.id == spec['task_id'])).mappings().first()
        task_value = dict(id=spec['task_id'], intake_id=spec['task_id'], proposal_id=None, parent=None,
            kind='task', title=spec['title'], purpose=spec['instruction'], acceptance=[
                'Compression-only PNG differences pass; real image and runtime changes fail.',
                'Approved artwork and production bytes remain unchanged; complete regression suite passes.'],
            model='Owner-authorized native task', created_at=execution.now())
        if not task:
            c.execute(backlog_items.insert().values(**task_value))
        elif any(task[key] != value for key, value in task_value.items() if key != 'created_at'):
            raise ValueError('The frozen native backlog task changed')
    grant = execution.issue(engine, owner_instruction, provenance, runtime)
    return execution.submit(engine, grant['id'], str(uuid5(UUID(spec['task_id']), ENGINE)))


def publication_policy(grant):
    spec = validate_spec(grant['snapshot']['native_task'])
    return dict(decision_id=spec['task_id'], budget=dict(publication='hold', publication_seconds=900),
        offer=dict(snapshot=dict(specification=spec)))


def checked(c, row, grant, *, active=True):
    from workbench.db import model_attempts
    from workbench.real_publication import DeliveryHeld
    from workbench.result_rejection import later_review
    if active:
        validate_authority(c, grant)
        if grant['revoked_at']:
            raise DeliveryHeld('Native task permission was revoked; checked history is retained')
    ledger = c.execute(select(model_attempts).where(model_attempts.c.id == row['fence'],
        model_attempts.c.execution_id == row['id'])).mappings().first()
    result = row['result'] or {}
    if (row['status'] != 'checked' or grant['binding'] != execution.digest(grant['snapshot'])
            or not ledger or ledger['outcome'] != 'checked' or ledger['verification'] != 'passed'
            or ledger['usage'] != result.get('usage') or result.get('verification') != 'passed'
            or result.get('inference_ran') is not True or not result.get('native_authorship')
            or later_review(c.connection.driver_connection, row['id'])):
        raise DeliveryHeld('Exact native authorship, independent checks and existing ledger are required')


def publication_preview(row, grant):
    spec = grant['snapshot']['native_task']
    result = row['result']
    body = '\n'.join([spec['instruction'], '', 'Changes',
        *['- ' + entry['path'] for entry in result['files']], '', 'Independent verification',
        '- Real gallery rebuild and all original gallery checks passed.',
        '- Identical PNG dimensions and RGBA pixels accepted after recompression.',
        '- Changed dimensions, channels, alpha, runtime textures, manifest and ArtData rejected.',
        '- Complete applicable Lua and Python regression inventory passed.', '',
        'Authorship: native Codex in its isolated disposable workspace.',
        'No artwork or production file changed. No merge, release or game installation.', '',
        'Execution: ' + row['id'], 'Base: ' + spec['contract']['base'],
        'Candidate: ' + result['candidate_id'],
        '<!-- workbench-delivery:' + row['id'] + ':' + result['candidate_id'] + ' -->'])
    if len(body.encode()) > 16384:
        raise ValueError('Native draft summary exceeds the existing publication bound')
    return dict(title=spec['title'], body=body, draft=True, category='Bug fix', task_id=spec['task_id'])


def task_context(c, intake_id, task_id):
    """Present the same native records through the existing task/run UI."""
    if intake_id != task_id:
        return None
    original = c.execute(select(intakes.c.payload).where(intakes.c.id == intake_id)).scalar_one_or_none()
    if not original or not original.get('native_task'):
        return None
    from workbench.db import real_publications
    rows = c.execute(select(real_execution_requests).join(real_execution_grants,
        real_execution_requests.c.grant_id == real_execution_grants.c.id).where(
            real_execution_grants.c.snapshot['native_task']['task_id'].as_string() == task_id)
        .order_by(real_execution_requests.c.created_at.desc())).mappings().all()
    runs = []
    for row in rows:
        publication = c.execute(select(real_publications).where(real_publications.c.execution_id == row['id'])).mappings().first()
        pub = None
        if publication:
            url = publication['state'].get('url')
            real = publication['state'].get('source') == 'github' and isinstance(url, str) and bool(re.fullmatch(
                r'https://github.com/Twich-Team/TwichUI/pull/[1-9][0-9]{0,9}', url))
            pub = dict(status=publication['status'], message=publication['message'],
                url=url if real else None, published=bool(real and publication['status'] == 'published'))
        result = row['result'] or {}
        recovering, notice = execution.recovery_notice(row['status'], result)
        runs.append(dict(id=row['id'], status=row['status'], message=notice or row['message'],
            verification=result.get('verification'), candidate_id=result.get('candidate_id'),
            current_task=True, recovery_pending=recovering, publication=pub))
    return dict(intake_id=intake_id, task_id=task_id, stage=rows[0]['status'] if rows else 'waiting',
        message=rows[0]['message'] if rows else 'Native task is authorized and waiting for its execution record.',
        current_approval=True, execution_configured=True, runs=runs, publication_allowed=False, accepted=False)


class Runtime(LegacyRuntime):
    def __init__(self, root, stop_event=None):
        self.root = Path(root)
        if (self.root.resolve() != self.root or self.root.is_symlink() or not self.root.is_dir()
                or self.root.stat().st_uid != os.getuid() or self.root.stat().st_mode & 0o077):
            raise ValueError('Private owned native evidence root required')
        self.recipe = validate_spec(json.loads((self.root / 'native-task.json').read_text()))
        self.execution_contract = self.recipe['contract']
        self.source, self.base = self.execution_contract['source'], self.execution_contract['base']
        self.paths = tuple(self.execution_contract['paths'])
        self.initial = self.root / 'base-manifest.json'
        self.oracle_source = VERIFIER
        self.stop_event, self.package, self.grant_deadline = stop_event, None, None
        self.native_authorization = self.recipe
        self.workspace = Path(json.loads((self.root / 'workspace-location.json').read_text())['path'])
        self.scratch = self.workspace.parent / 'scratch'

    def identity(self):
        scripts = Path(__file__).resolve().parents[2] / 'scripts'
        return dict(engine_version=ENGINE, model=self.recipe['strategy']['stages'][0]['model'],
            workspace=str(self.workspace), owner_uid=os.getuid(), contract=execution.digest(self.execution_contract),
            native_task_sha256=sha((self.root / 'native-task.json').read_bytes()),
            base_manifest_sha256=sha(self.initial.read_bytes()), oracle_sha256=sha(VERIFIER.read_bytes()),
            native_baseline_sha256=sha((self.root / 'native-baseline.json').read_bytes()),
            native_binaries=boundary.BINARY_HASHES,
            helper_sha256={path.name: sha(path.read_bytes()) for path in Path(__file__).parent.glob('*.py')},
            connector_sha256={name: sha((scripts / name).read_bytes()) for name in
                ('native_connector.py', 'codex_connector.py', 'strategy_connector.py')})

    def preflight(self):
        from workbench import real_confine
        source = Path(self.source)
        original = json.loads(self.initial.read_text())
        clean = (git(source, 'rev-parse', 'HEAD').decode().strip() == self.base
            and not git(source, 'status', '--porcelain=v1', '--ignored')
            and inventory(source, self.base) == {p: {k: v for k, v in e.items() if k != 'sha256'} for p, e in original.items()})
        # No original-program generation or successful original suite is required.
        return [dict(name='exact clean native source', status='passed' if clean else 'blocked'),
            dict(name='pristine disposable native input', status='passed' if self.pristine_input() else 'blocked'),
            dict(name='pinned native client', status='passed' if boundary.binaries_valid() else 'blocked'),
            dict(name='independent verification cgroup', status='passed' if Path(real_confine.root()).is_dir() else 'blocked')]

    def pristine_input(self):
        try:
            baseline = json.loads((self.root / 'native-baseline.json').read_text())
            current, _ = boundary.scan(self.workspace)
            return current == baseline
        except (OSError, ValueError):
            return False

    def enforce_storage(self):
        total = sum(p.stat().st_size for p in self.root.rglob('*') if p.is_file() and not p.is_symlink())
        if total > 64 << 20:
            raise ValueError('Native evidence exceeded storage allowance')

    def admission_gate(self, pending, grant):
        from workbench.codex_provider import connector_path
        previous = next((gate for gate in (pending['result'] or {}).get('gates', [])
            if gate.get('name') == 'native included-allowance admission'), {})
        if previous.get('status') == 'blocked' and previous.get('decision', {}).get('recheck_at', 0) > time.time():
            return previous
        waiting = dict(previous.get('waiting_since', {}))
        self.native_waiting_since = waiting
        endpoint = connector_path()
        if not endpoint:
            return dict(name='native included-allowance admission', status='blocked')
        request = dict(operation='native-codex-observe', request_id=pending['id'],
            fence=pending['fence'], root=str(self.root), waiting_since=waiting)
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(35)
            connection.connect(str(endpoint))
            connection.sendall(json.dumps(request).encode() + b'\n')
            data = connection.makefile('rb').readline(65537)
        if not data.endswith(b'\n') or len(data) > 65536:
            raise ValueError('Native allowance observation unavailable')
        result = json.loads(data)
        decision = result.get('decision', {})
        self.native_decision = decision
        if decision.get('state') == 'waiting' and decision.get('stage_id'):
            waiting.setdefault(decision['stage_id'], time.time())
        self.native_waiting_since = waiting
        return dict(name='native included-allowance admission',
            status='passed' if decision.get('state') == 'selected' else 'blocked',
            decision=decision, waiting_since=waiting)

    def execute(self, identity, fence, permitted):
        if not permitted():
            raise ValueError('Native permission withdrawn')
        baseline = json.loads((self.root / 'native-baseline.json').read_text())
        request = dict(operation='native-codex', request_id=identity, fence=fence, root=str(self.root),
            waiting_since=getattr(self, 'native_waiting_since', {}))
        from workbench.codex_provider import connector_path
        endpoint = connector_path()
        if not endpoint:
            raise ValueError('Native provider connector unavailable')
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(2)
            connection.connect(str(endpoint))
            connection.sendall(json.dumps(request).encode() + b'\n')
            connection.settimeout(.2)
            data = bytearray()
            deadline = time.monotonic() + 1000
            while not data.endswith(b'\n'):
                if not permitted() or time.monotonic() > deadline:
                    raise ValueError('Native execution canceled or timed out; reconcile saved native unit')
                try:
                    chunk = connection.recv(65536)
                except TimeoutError:
                    continue
                if not chunk or len(data) + len(chunk) > 65536:
                    raise ValueError('Native connector outcome is unknown')
                data.extend(chunk)
        response = json.loads(data)
        if response.get('state') != 'completed':
            raise ValueError('Native admission did not dispatch: ' + str(response.get('decision', response.get('error')))[:600])
        provider = response['result']
        if not provider.get('settled') or not provider.get('terminal') or provider.get('exit_code') != 0 or provider.get('stop_reason'):
            raise ValueError('Native coding did not settle successfully')
        if not permitted():
            raise ValueError('Native authority changed before export')
        accepted = boundary.export(self.workspace, baseline, self.paths, self.root / 'accepted-native')
        if not accepted['changes'] or any(path not in accepted['manifest'] for path in accepted['changes']):
            raise ValueError('Native session produced no authorized source changes')
        return self.verify_candidate(identity, fence, accepted, provider, permitted)

    def verify_candidate(self, identity, fence, accepted, provider, permitted):
        from workbench import real_confine
        from workbench.candidate_review import compare
        self.phase()
        verification_deadline = time.monotonic() + self.execution_contract['limits']['acceptance_s']
        real_confine.USAGE_LOG = str(self.root / 'usage.jsonl')
        # Full original source stays private to the independent verifier. The
        # coding agent received only the frozen disclosure manifest.
        parent = Path(tempfile.mkdtemp(prefix='workbench-verify-native-'))
        try:
            candidate = parent / 'candidate'
            original = materialize(self.source, self.base, candidate)
            for path in accepted['changes']:
                (candidate / path).write_bytes((self.root / 'accepted-native' / path).read_bytes())
            manifest_tree = tree(candidate)
            original_path = parent / 'original-test.py'
            original_path.write_bytes(git(self.source, 'show', self.base + ':' + FOCUSED))
            suite = sorted(Path(p).name for p in original if p.startswith('tests/test_') and p.count('/') == 1 and p.endswith(('.lua', '.py')))
            suite_path = parent / 'suite.json'
            suite_path.write_text(json.dumps(suite))
            commands = [['python3', '-B', '-m', 'unittest', '-q', 'test_reminder_gallery'],
                ['python3', '-I', '-B', str(VERIFIER), str(candidate), str(original_path)],
                ['python3', '-B', str(Path(__file__).with_name('repair_suite.py')), str(suite_path)]]
            checks = []
            for index, command in enumerate(commands):
                if not permitted():
                    raise ValueError('Authority changed before independent check')
                from workbench.verification_process import VerificationProcess
                remaining = verification_deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError('Native independent verification reached its frozen timeout')
                result = real_confine.run(command, str(candidate / 'tests') if index == 0 else str(candidate),
                    timeout=min(600, remaining), env={'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent',
                        'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1'},
                    ro=[str(candidate), str(VERIFIER), str(original_path), str(suite_path), str(Path(__file__).with_name('repair_suite.py'))],
                    private_loopback=True, verification=VerificationProcess(tuple(command), str(candidate)),
                    stop_event=self.stop_event)
                result['command'] = command
                durable(self.root / ('native-check-' + str(index) + '.json'), json.dumps(result, sort_keys=True).encode())
                checks.append(result)
                safe = not any(result['usage'].get(key) for key in ('leaked_after_kill', 'oom_kill', 'pids_limit_hit'))
                safe = safe and result['usage'].get('cgroup_removed') is True
                if result['status'] != 'pass' or result['rc'] != 0 or not safe:
                    self.check_failure = dict(name=['Gallery rebuild', 'Independent image behavior', 'Complete regression suite'][index],
                        diagnostic=result['out'][-6000:])
                    raise ValueError('Independent native candidate check failed: ' + str(index))
            if (tree(candidate) != manifest_tree or not permitted()
                    or not checks[-1]['out'].strip().endswith(f'{len(suite)} passed, 0 failed')):
                raise ValueError('Exact candidate or complete applicable regression inventory changed')
            # Construct patch only from independently exported, authorized bytes.
            files, patches = [], []
            for path in accepted['changes']:
                before = git(self.source, 'show', self.base + ':' + path)
                after = (candidate / path).read_bytes()
                entry = compare(path, before, after, input_limit=65536)
                if entry['kind'] != 'modified' or entry.get('truncated') or not entry.get('diff'):
                    raise ValueError('Complete readable native file comparison required')
                files.append(entry)
                import difflib
                patches.append(''.join(difflib.unified_diff(before.decode().splitlines(True), after.decode().splitlines(True),
                    fromfile='a/' + path, tofile='b/' + path)))
            patch = ''.join(patches).encode()
            changed_lines = sum(1 for line in patch.decode().splitlines()
                if line.startswith(('+', '-')) and not line.startswith(('+++', '---')))
            if len(patch) > 65536 or changed_lines > self.execution_contract['limits']['changed_lines']:
                raise ValueError('Native repair exceeded approved patch allowance')
            manifest = dict(request_id=identity, fence=fence, base=self.base, tree=manifest_tree,
                patch_sha256=sha(patch), oracle_sha256=sha(VERIFIER.read_bytes()),
                native_task_sha256=sha((self.root / 'native-task.json').read_bytes()),
                native_provider_sha256=sha((self.root / 'native-provider-result.json').read_bytes()),
                original_manifest_sha256=sha(self.initial.read_bytes()), suite=suite,
                native_export=accepted, checks_sha256=[sha((self.root / ('native-check-' + str(i) + '.json')).read_bytes()) for i in range(3)])
            candidate_id = sha(json.dumps(manifest, sort_keys=True).encode())
            frozen = self.root / 'candidates' / candidate_id
            durable(frozen / 'manifest.json', json.dumps(manifest, sort_keys=True).encode())
            durable(frozen / 'changes.patch', patch)
            for path in accepted['changes']:
                durable(frozen / 'files' / path, (candidate / path).read_bytes())
            result = dict(candidate_id=candidate_id, manifest=manifest, files=files, checks=checks,
                usage=provider['usage'], inference_ran=True, verification='passed',
                operation_kind=ENGINE,
                native_authorship=dict(engine_version=ENGINE, request_id=identity,
                    provider_receipt_sha256=manifest['native_provider_sha256']))
            durable(frozen / 'result.json', json.dumps(result, sort_keys=True).encode())
            return result
        finally:
            shutil.rmtree(parent)

    def verify_frozen(self, result):
        try:
            frozen = self.root / 'candidates' / result['candidate_id']
            manifest = json.loads((frozen / 'manifest.json').read_text())
            return (sha((frozen / 'manifest.json').read_bytes()) == result['candidate_id']
                and manifest == result['manifest'] and json.loads((frozen / 'result.json').read_text()) == result
                and sha((frozen / 'changes.patch').read_bytes()) == manifest['patch_sha256']
                and manifest['native_task_sha256'] == sha((self.root / 'native-task.json').read_bytes())
                and manifest['native_provider_sha256'] == sha((self.root / 'native-provider-result.json').read_bytes())
                and all(sha((frozen / 'files' / e['path']).read_bytes()) == manifest['tree'][e['path']] for e in result['files'])
                and all(e['path'] in self.paths for e in result['files'])
                and manifest['checks_sha256'] == [sha((self.root / ('native-check-' + str(i) + '.json')).read_bytes()) for i in range(3)])
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def verify_checks(self, result, grant):
        return (self.verify_frozen(result) and len(result['checks']) == 3
            and all(c['status'] == 'pass' and c['rc'] == 0 and c['usage'].get('cgroup_removed') is True
                and not any(c['usage'].get(key) for key in ('leaked_after_kill', 'oom_kill', 'pids_limit_hit'))
                for c in result['checks'])
            and self.identity() == grant['snapshot']['runtime']
            and result['manifest']['original_manifest_sha256'] == sha(self.initial.read_bytes())
            and result['usage'] == json.loads((self.root / 'native-provider-result.json').read_text())['usage'])

    def failure_evidence(self, identity, fence):
        provider = self.root / 'native-provider-result.json'
        if provider.exists():
            result = json.loads(provider.read_text())
            known = (result['usage'].get('values') or {}).get('total_tokens')
            return dict(usage=result['usage'], inference_ran=True if result.get('terminal') or type(known) is int and known > 0 else None)
        return dict(usage=dict(status='unavailable', values=None, requests_observed=None,
            provenance=dict(method='Native session outcome unavailable', request_id=identity, fence=fence)),
            inference_ran=None if (self.root / 'native-process-started.json').exists() else False)

    def cleanup(self, row):
        from workbench.native_codex import stop_unit, unit_name
        return stop_unit(unit_name(row['id']))
