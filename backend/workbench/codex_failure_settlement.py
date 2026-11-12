"""Fence a closed, output-free Codex response without inventing token usage.

This narrow recovery covers a pristine first feature attempt whose failure
handoff was refused by storage. It grants no execution and changes no old row.
"""
from datetime import datetime, timedelta
import json
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from workbench import everyday, real_execution
from workbench.db import model_attempts, real_execution_grants, real_execution_requests
from workbench.investigation_recovery import AUTHORITY
from workbench.feature_context_storage import read
from workbench.real_workspace import Runtime, durable, sha, tree, git
from workbench.repair_continuation import private_json
from workbench.visual_scope import digest


def terminal(events, request_sha, start, finish):
    """Positive capability, cancellation and closure evidence; absence is not proof."""
    requests = [e for e in events if e.get('phase') == 'request']
    isolated = [e.get('capabilities', {}) for e in events if e.get('phase') == 'isolation-verified']
    turns = [e for e in events if e.get('phase') == 'turn-started']
    ends = [e for e in events if e.get('phase') == 'lifecycle-terminal']
    held = [e for e in events if e.get('phase') == 'turn-held']
    closed = [e for e in events if e.get('phase') == 'connection-closed']
    interrupts = [e for e in events if e.get('phase') == 'interrupt-result']
    if not all(len(v) == 1 for v in (requests, isolated, turns, ends, held, closed, interrupts)):
        raise ValueError('One closed interrupted Codex turn required')
    capability = isolated[0]
    if (requests[0].get('request_sha256') != request_sha
            or capability.get('kind') != 'tool-free-codex-investigation-v1'
            or any(capability.get(k) is not False for k in (
                'source_writes', 'execution_dispatch', 'external_action_tools', 'owner_config_mounted'))
            or any(capability.get(k) is not True for k in ('empty_work_directory', 'shell_disabled'))
            or capability.get('sandbox') != 'readOnly' or capability.get('approval_policy') != 'never'
            or not turns[0].get('thread_id') or not turns[0].get('turn_id')
            or any(ends[0].get(k) != turns[0][k] for k in ('thread_id', 'turn_id'))
            or ends[0].get('status') != 'interrupted' or ends[0].get('usage') is not None
            or interrupts[0].get('acknowledged') is not True or interrupts[0].get('status') != 'interrupted'
            or held[0].get('terminal_status') != 'interrupted' or held[0].get('partial') is not False
            or held[0].get('usage') is not None or held[0].get('output_bytes') != 0
            or held[0].get('output_sha256') != sha(b'') or held[0].get('output_excerpt') != ''
            or closed[0].get('status') != 'local-producer-closed'
            or events[-1] != closed[0] or any(e.get('phase') == 'first-output' for e in events)):
        raise ValueError('Codex cancellation, isolation or output is not settled')
    times = [datetime.fromisoformat(e['time']) for e in events]
    # The local stop can settle its database row before the connector finishes
    # its existing bounded interrupt drain. Require dispatch in the original
    # interval and positive terminal closure within that 20-second drain.
    end = datetime.fromisoformat(finish)
    if (times != sorted(times) or not datetime.fromisoformat(start) <= times[0] <= end
            or datetime.fromisoformat(turns[0]['time']) > end
            or times[-1] > end+timedelta(seconds=20)):
        raise ValueError('Connector turn does not belong to this execution interval')


def inspect(c, row, grant, projection):
    snapshot = grant['snapshot']
    root = Path(snapshot['recovery']['root'])
    runtime = Runtime(root)
    usage = row['result'].get('usage', {})
    ledger = c.execute(select(model_attempts).where(model_attempts.c.id == row['fence'],
        model_attempts.c.execution_id == row['id'])).mappings().one()
    if (not snapshot['runtime'].get('codex_selection')
            or runtime.recipe.get('repair_cycle') != 'scoped-feature-v1'
            or row['status'] != 'failed' or not row['finished_at'] or not grant['revoked_at']
            or grant['binding'] != digest(snapshot) or ledger['backend'] != 'codex'
            or ledger['outcome'] != 'failed' or not ledger['finished_at'] or ledger['usage'] != usage
            or usage != dict(status='unavailable', units='tokens', values=None)
            or projection['execution_id'] != row['id'] or projection['fence'] != row['fence']
            or projection['grant_binding'] != grant['binding']
            or projection['model'] != snapshot['runtime']['codex_selection']['model']
            or projection['effort'] != snapshot['runtime']['codex_selection']['effort']
            or not runtime.owns_recovery(grant)):
        raise ValueError('Exact failed feature execution and immutable unavailable usage required')
    original = private_json(runtime.initial)
    if (tree(runtime.workspace) != original or digest(original) != projection['source_tree_sha256']
            or git(runtime.workspace, 'status', '--porcelain=v1', '--ignored').strip()
            or any(root.glob('codex-output-*')) or any(root.glob('codex-turn-*'))
            or (root/'edit-batches').exists() or (root/'candidates').exists()):
        raise ValueError('Only an unchanged first attempt with no accepted output qualifies')
    owner_file = root/('ownership-'+row['id']+'.json')
    owner = private_json(owner_file)
    group = Path(owner['cgroup'])
    if (real_execution.alive(owner['owner']) or not group.is_relative_to('/sys/fs/cgroup')
            or not group.name.startswith('workbench-real-codex-')
            or group.exists() and any(p.read_text().strip() for p in group.rglob('cgroup.procs'))):
        raise ValueError('Old implementation owner or descendants remain active')
    controller = root.parent/'lease-controller-result.json'
    if private_json(controller).get('restoration', {}).get('restored') is not True:
        raise ValueError('Controller restoration is unconfirmed')
    receipt = str(UUID(projection['connector_receipt']))
    paths = {Path(p)/'artifacts/codex-evidence'/(receipt+'.jsonl')
             for p in everyday.configuration(c.engine).evidence_roots}
    found = [p for p in paths if p.exists()]
    if len(found) != 1:
        raise ValueError('Unique native connector evidence required')
    raw = read(found[0], 65536)
    if len(raw) > 65536 or sha(raw) != projection['connector_evidence_sha256']:
        raise ValueError('Connector evidence changed')
    terminal([json.loads(line) for line in raw.splitlines()], projection['request_sha256'],
        ledger['started_at'], row['finished_at'])
    authority = everyday.ROOT/'docs/OWNER_READONLY_RECOVERY_20261108.md'
    if sha(authority.read_bytes()) != AUTHORITY:
        raise ValueError('Standing read-only recovery authority changed')
    return dict(kind='closed-output-free-codex-feature-failure-v1', execution_id=row['id'],
        fence=row['fence'], grant_binding=grant['binding'], result_binding=digest(row['result']),
        projection=projection, owner_sha256=sha(owner_file.read_bytes()),
        controller_sha256=sha(controller.read_bytes()), authority_sha256=AUTHORITY,
        old_output_reusable=False, original_usage_unchanged=True, unknown_usage_reserved=True)


def load(c, row, grant):
    path = Path(grant['snapshot']['recovery']['root'])/'codex-readonly-settlement.json'
    try:
        saved = private_json(path, 65536)
        actual = inspect(c, row, grant, saved['projection'])
        if saved != dict(**actual, binding=digest(actual)):
            return None
        return saved
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save(engine, identity, projection):
    """Administrative reconciliation; caller supplies an exact request-hash projection.

    Historical projections are reconstructed from the executed native prompt in
    a hash-only, transport-disabled diagnostic. No old result/ledger is updated.
    """
    with engine.connect() as c:
        row = c.execute(select(real_execution_requests).where(real_execution_requests.c.id == identity)).mappings().one()
        grant = c.execute(select(real_execution_grants).where(real_execution_grants.c.id == row['grant_id'])).mappings().one()
        proof = inspect(c, row, grant, projection)
        proof = dict(**proof, binding=digest(proof))
        durable(Path(grant['snapshot']['recovery']['root'])/'codex-readonly-settlement.json',
            json.dumps(proof, sort_keys=True).encode())
        return proof
