"""Create-only completion evidence, not an executor or execution permission."""

from datetime import datetime, timezone
import json
import os
import re

from workbench import real_execution as job


def _path(runtime, identity):
    from uuid import UUID

    UUID(identity)
    return runtime.root / ("completion-" + identity + ".json")


def _verified_checks(runtime, grant, result):
    from workbench.real_workspace import PRELUDE, sha, suite_inventory
    from workbench.session_authorship import usage_eligible

    if grant['snapshot'].get('contract', {}).get('engine_version') == 'native-agent-v1':
        from workbench.native_agent import Runtime as NativeRuntime
        return type(runtime) is NativeRuntime and runtime.verify_checks(result, grant)

    if grant['snapshot'].get('operation_kind')=='feature-verification-v1':
        return bool(getattr(runtime,'operation_kind',None)=='feature-verification-v1'
            and runtime.verify_checks(result,grant)
            and result['manifest']['oracle_sha256']==grant['snapshot']['runtime']['oracle_sha256']
            and result['manifest']['original_manifest_sha256']==grant['snapshot']['runtime']['base_manifest_sha256'])

    checks = result.get("checks", [])
    expected = [["luac5.1", "-p", p] for p in job.PATHS] + [
        ["lua5.1", "-e", PRELUDE, "test_chronicle_journey.lua"],
        ["lua5.1", "-e", PRELUDE, str(runtime.root / "oracle.lua")],
        ["bash", "tests/run.sh"],
    ]
    if getattr(runtime,'recipe',None):
        expected = runtime.expected_checks()
    return (
        len(checks) == len(expected)
        and all(
            c.get("status") == "pass" and c.get("rc") == 0 and c.get("command") == command
            for c, command in zip(checks, expected)
        )
        and suite_inventory(checks[-1], runtime.expected_suite())
        and sha((runtime.root / "oracle.lua").read_bytes())
        == result["manifest"]["oracle_sha256"]
        == grant["snapshot"]["runtime"]["oracle_sha256"]
        and sha(runtime.initial.read_bytes())
        == grant["snapshot"]["runtime"]["base_manifest_sha256"]
        and result["manifest"].get("completion_context_sha256")
        == grant["snapshot"]["runtime"].get("completion_context_sha256")
        and usage_eligible(grant['snapshot'],result)
        and (not grant['snapshot'].get('workflow_task') or
             (result['manifest'].get('workflow_task_binding') == job.digest(grant['snapshot']['workflow_task'])
              and result['manifest'].get('workflow_task') == dict(
                  intake_id=grant['snapshot']['workflow_task']['intake_id'],
                  task_id=grant['snapshot']['workflow_task']['task']['id'])))
        and all(f["path"] in grant["snapshot"]["contract"]["paths"] for f in result["files"])
    )


def save(runtime, grant, identity, fence, result, completed_at):
    if (
        not job.valid(grant)
        or not runtime.verify_frozen(result)
        or not _verified_checks(runtime, grant, result)
    ):
        raise ValueError("Current grant and exact frozen completion required")
    manifest = result["manifest"]
    if (
        result["verification"] != "passed"
        or manifest["request_id"] != identity
        or manifest["fence"] != fence
        or manifest["base"] != grant["snapshot"]["contract"]["base"]
    ):
        raise ValueError("Completion identity differs from request")
    evidence = dict(
        version=1,
        request_id=identity,
        fence=fence,
        grant_id=grant["id"],
        grant_binding=grant["binding"],
        candidate_id=result["candidate_id"],
        result_digest=job.digest(result),
        completed_at=completed_at,
    )
    data = json.dumps(evidence, sort_keys=True).encode()
    _publish(runtime, _path(runtime, identity), data)


def _publish(runtime, target, data):
    """The existing bounded, fsynced, create-only handoff publication boundary."""
    from workbench.real_workspace import durable

    runtime.enforce_storage()
    if sum(p.stat().st_size for p in runtime.root.rglob("*") if p.is_file()) + len(data) > 64 << 20:
        raise ValueError("Completion evidence exceeds existing storage ceiling")
    if getattr(runtime, "package", None):
        from workbench.real_package import Package

        usage = Package(
            runtime.package["package"], runtime.package["baseline_sha256"]
        ).enforce_storage()
        if (
            usage["package_bytes"] + len(data) > 128 << 20
            or usage["real_run_bytes"] + len(data) > 64 << 20
        ):
            raise ValueError("Completion evidence exceeds package reservation")
    staging = target.with_suffix(".pending")
    durable(staging, data)
    runtime.enforce_storage()
    # Publish only after the staged bytes passed all checks. A hard link is
    # atomic and create-only: readers never see partial or rejected evidence.
    try:
        os.link(staging, target)
    except FileExistsError:
        if target.is_symlink() or target.read_bytes() != data:
            raise ValueError("Conflicting immutable completion seal") from None
    staging.unlink()
    fd = os.open(runtime.root, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def load(runtime, row, grant):
    """Read-only proof inspection; never cleanup, executable checks or replay."""
    try:
        path = _path(runtime, row["id"])
        if path.is_symlink() or path.stat().st_size > 4096:
            return None
        evidence = json.loads(path.read_text())
        candidate = evidence["candidate_id"]
        if not re.fullmatch("[0-9a-f]{64}", candidate):
            return None
        at = datetime.fromisoformat(evidence["completed_at"])
        if (
            at.utcoffset() is None
            or not datetime.fromisoformat(grant["issued_at"])
            <= at
            <= datetime.fromisoformat(grant["expires_at"])
            or (grant["revoked_at"] and datetime.fromisoformat(grant["revoked_at"]) <= at)
        ):
            return None
        if (
            evidence["version"] != 1
            or evidence["request_id"] != row["id"]
            or evidence["fence"] != row["fence"]
            or evidence["grant_id"] != grant["id"]
            or evidence["grant_binding"] != grant["binding"]
            or job.digest(grant["snapshot"]) != grant["binding"]
        ):
            return None
        result_path = runtime.root / "candidates" / candidate / "result.json"
        if (
            not result_path.resolve().is_relative_to(runtime.root.resolve())
            or any(
                p.is_symlink()
                for p in [result_path, *result_path.parents]
                if runtime.root in p.parents
            )
            or result_path.stat().st_size > 4 << 20
        ):
            return None
        result = json.loads(result_path.read_text())
        manifest = result["manifest"]
        if (
            job.digest(result) != evidence["result_digest"]
            or not runtime.verify_frozen(result)
            or result["verification"] != "passed"
            or manifest["request_id"] != row["id"]
            or manifest["fence"] != row["fence"]
            or manifest["base"] != grant["snapshot"]["contract"]["base"]
            or not _verified_checks(runtime, grant, result)
        ):
            return None
        return dict(result=result, completed_at=evidence["completed_at"])
    except OSError, ValueError, KeyError, TypeError:
        return None


def _failure_path(runtime, identity):
    return _path(runtime, identity).with_name('outcome-' + identity + '.json')


def save_failure(runtime, grant, identity, fence, result, completed_at, owner):
    """Durably retain a returned failure, including partial/unavailable usage.

    This can never stand in for a verified-completion seal. Failure after expiry
    is still an outcome that must be delivered, not renewed execution authority.
    """
    from uuid import UUID

    UUID(fence)
    if (job.digest(grant['snapshot']) != grant['binding'] or
            not runtime.owns_recovery(grant) or result.get('verification') == 'passed'):
        raise ValueError('Exact failed outcome and owned recovery directory required')
    if result.get('candidate_id') and not runtime.verify_frozen(result):
        raise ValueError('Failed candidate integrity mismatch')
    at = datetime.fromisoformat(completed_at)
    if (at.utcoffset() is None or at < datetime.fromisoformat(grant['issued_at']) or
            at > datetime.now(timezone.utc)):
        raise ValueError('Invalid failure timestamp')
    evidence = dict(version=1, status='failed', request_id=identity, fence=fence,
                    grant_id=grant['id'], grant_binding=grant['binding'], owner=owner,
                    result=result, result_digest=job.digest(result), completed_at=completed_at)
    data = json.dumps(evidence, sort_keys=True, allow_nan=False).encode()
    if len(data) > 4 << 20:
        raise ValueError('Failure handoff exceeds bounded result size')
    _publish(runtime, _failure_path(runtime, identity), data)


def load_failure(runtime, row, grant):
    """Inspect a returned failure only. No checks, inference or success promotion."""
    try:
        path = _failure_path(runtime, row['id'])
        if (not runtime.owns_recovery(grant) or path.is_symlink() or
                path.stat().st_size > 4 << 20):
            return None
        evidence = json.loads(path.read_text())
        if not isinstance(evidence, dict) or not isinstance(evidence.get('result'), dict):
            return None
        result = evidence['result']
        at = datetime.fromisoformat(evidence['completed_at'])
        if (evidence['version'] != 1 or evidence['status'] != 'failed' or
                evidence['request_id'] != row['id'] or evidence['fence'] != row['fence'] or
                evidence['grant_id'] != grant['id'] or evidence['grant_binding'] != grant['binding'] or
                job.digest(grant['snapshot']) != grant['binding'] or
                evidence['owner'] != row['result']['owner'] or
                job.digest(result) != evidence['result_digest'] or result.get('verification') == 'passed' or
                at.utcoffset() is None or at < datetime.fromisoformat(grant['issued_at']) or
                at > datetime.now(timezone.utc) or
                (result.get('candidate_id') and not runtime.verify_frozen(result))):
            return None
        return dict(status='failed', result=result, completed_at=evidence['completed_at'])
    except OSError, ValueError, KeyError, TypeError, RecursionError:
        return None
