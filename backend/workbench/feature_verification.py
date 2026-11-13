"""Combined, model-free feature checks under the existing real execution owner.

The operation receives only actual checked child ancestry and an independently
prepared original-source oracle. It has no model, edit tool, fallback or provider
controller. Its frozen result retains child authorship/usage without fabricating
an implementation ledger event for local checks.
"""

import json
import re
import os
from pathlib import Path
import subprocess
import time
from datetime import datetime, timezone, timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select

from workbench import feature_checks, feature_steps, feature_source, task_execution, intake
from workbench import real_execution as owner
from workbench.db import (
    real_execution_requests as requests,
    real_execution_grants as grants,
    model_attempts,
    planning_jobs,
    task_sessions,
    write_transaction,
)
from workbench.real_workspace import (
    Runtime as CodingRuntime,
    durable,
    sha,
    tree,
    git,
    PRELUDE,
    suite_inventory,
    require_safe_check,
)
from workbench.trusted_preparation import owned, configuration
from workbench.visual_scope import digest

KIND = "feature-verification-v1"


class OriginalObservationsUnavailable(ValueError):
    pass


def is_grant(grant):
    return bool(grant and grant["snapshot"].get("operation_kind") == KIND)


def checked_chain(c, decision_id, records, feature_input, *, active=True):
    """Re-read actual ledgers/native completion and exact last input, never flags."""
    approval, permission = feature_steps.parent(c, decision_id, active=active)
    if not records or not feature_input:
        raise ValueError(
            "Combined feature verification requires actual checked implementation changes."
        )
    contributions = []
    paths = set()
    for item in records:
        row, grant, linked, result = feature_source._checked(c, item["execution_id"], active=active)
        if (
            linked["approval"]["id"] != decision_id
            or linked["task"]["id"] != item["task_id"]
            or grant["snapshot"]["task_budget"]["offer"]["id"] != item["offer_id"]
        ):
            raise ValueError("A combined contribution belongs to different feature work.")
        contributions.append(
            dict(
                task_id=item["task_id"],
                execution_id=row["id"],
                fence=row["fence"],
                grant_binding=grant["binding"],
                candidate_id=result["candidate_id"],
                result_binding=digest(result),
                usage=result["usage"],
                provider=grant["snapshot"]["contract"]["provider"],
            )
        )
        paths.update(grant["snapshot"]["contract"]["paths"])
    linked = task_execution.snapshot(c, approval["intake_id"], records[-1]["task_id"], decision_id)
    carried = feature_source.prerequisite_tasks(c, feature_input, linked, configuration(c.engine), active=active)
    if (
        carried != {r["task_id"] for r in records}
        or feature_input != records[-1]["input"]
        or not 1 <= len(paths) <= 18
    ):
        raise ValueError(
            "Combined source does not carry exactly the checked feature contributions."
        )
    return approval, permission, contributions, sorted(paths)


def specification(c, job):
    payload = job["payload"]
    saved = payload["current_step_scope"]
    feature_steps.validate_scope(c, saved)
    prepared = payload["independent_check_preparation"]
    composed = payload.get('evidence_composition')
    if composed:
        compiled, oracle = composed_checks(c, job)
    else:
        compiled, oracle = feature_checks.compile(
            prepared["packet"], prepared["proposed_program"]["program"]
        )
    if (
        payload["phase"] != "independent_checks_ready"
        or saved["scope"]["role"] != "verification"
        or not composed and (prepared["proposed_program"] != compiled
            or not prepared["review"]["ready"]
            or prepared["review"]["program_binding"] != compiled["binding"])
    ):
        raise ValueError(
            "Combined verification needs its exact independently prepared and reviewed observations."
        )
    records = payload["completed_steps"]
    actual = records[-1]["input"] if records else None
    approval, permission, contributions, paths = checked_chain(
        c, saved["decision_id"], records, actual
    )
    from workbench.feature_check_preparation import request_limit, finished_history
    if (len(prepared['history']) > request_limit(c, job, permission, active=False)
            or not finished_history(c, job, prepared['history'])):
        raise ValueError(
            "Independent preparation exceeded its original permission or has uncertain work."
        )
    # The publication source/base remains the ORIGINAL approved repository. The
    # last child commit is a separate input, never relabelled as original main.
    from workbench.regression_preparation import repository

    source, base, _ = repository()
    if base != permission["scope"]["feature_base"]:
        raise ValueError("Original feature base moved before combined verification.")
    from workbench.feature_run_recovery import time_authority
    authority_reference, allowed_end = time_authority(c,approval,permission)
    value = dict(
        version=1,
        operation_kind=KIND,
        decision_id=approval["id"],
        parent_binding=approval["binding"],
        verification_task_id=saved["task_id"],
        scope=saved,
        original_source=str(source),
        original_base=base,
        feature_input=actual,
        records=records,
        contributions=contributions,
        paths=paths,
        independent_checks=compiled,
        independent_packet=prepared["packet"],
        independent_review=(dict(kind='audited-evidence-composition', binding=composed['binding'])
                            if composed else prepared["review"]),
        identity=configuration(c.engine)["identity"],
        expires_at=allowed_end,
        **({'time_authority':authority_reference} if authority_reference != 'original-parent-run' else {}),
        attempt=payload.get("feature_verification_attempt", 1),
    )
    return {**value, "binding": digest(value)}, oracle


def composed_checks(c, job):
    """Audit native evidence under the existing completion and ancestry owners."""
    payload = job['payload']
    receipt = payload['evidence_composition']
    value = {k:v for k,v in receipt.items() if k != 'binding'}
    prepared = payload['independent_check_preparation']
    if (receipt['binding'] != digest(value)
            or receipt['scope_binding'] != receipt['scope']['scope_binding']
            or receipt['history_binding'] != digest(prepared)
            or receipt['records_binding'] != digest(payload['completed_steps'])
            or not re.fullmatch('[a-f0-9]{64}', receipt['authorization_sha256'])):
        raise ValueError('Composed verification history, scope or checked source changed.')
    composition = receipt['composition']
    feature_steps.validate_scope(c, receipt['scope'], active=False)
    compiled, oracle = feature_checks.compile(prepared['packet'], receipt['program'],
        composition=composition, retained=True)
    records = payload['completed_steps']
    approval, permission, contributions, paths = checked_chain(c, payload['feature_decision_id'],
        records, records[-1]['input'], active=False)
    if (composition['contributions_binding'] != digest(contributions)
            or composition['feature_input_binding'] != digest(records[-1]['input'])
            or composition['paths'] != paths):
        raise ValueError('Composed receipts belong to different checked source or ancestry.')
    native = {}
    for record in records:
        row, grant, _, result = feature_source._checked(c, record['execution_id'], active=False)
        native[row['id']] = row, grant, result
    checked_source = Path(records[-1]['input']['source'])
    patch = subprocess.check_output(['git', '-C', str(checked_source), 'diff', '--no-ext-diff',
        '--no-textconv', '--binary', prepared['packet']['base'], records[-1]['input']['commit']], timeout=30)
    control_hashes = {
        'checked-ancestry':digest(contributions),
        'approved-diff':sha(patch),
        'full-suite':digest([r[2]['checks'][-1] for r in native.values()]),
        'history-and-permission':digest(dict(approval=dict(approval), permission=permission,
            history=prepared, records=records)),
    }
    for evidence in composition['evidence']:
        if evidence['kind'] == 'control' and evidence.get('sha256') != control_hashes[evidence['control']]:
            raise ValueError('Application-owned source, suite or permission evidence changed.')
        if evidence['kind'] != 'native':
            continue
        if evidence['execution_id'] not in native:
            raise ValueError('A reused assertion is not from this checked feature chain.')
        row, grant, result = native[evidence['execution_id']]
        frozen = Path(grant['snapshot']['recovery']['root'])/'candidates'/result['candidate_id']/'files'/evidence['path']
        source = frozen.read_bytes()
        lines = source.decode().splitlines(keepends=True)
        assertion = ''.join(lines[evidence['start']-1:evidence['end']]).encode()
        final_source = subprocess.check_output(['git', '-C', str(checked_source),
            'show', records[-1]['input']['commit']+':'+evidence['path']], timeout=30)
        if (evidence['result_binding'] != digest(result)
                or evidence['file_sha256'] != sha(source)
                or evidence['sha256'] != sha(assertion)
                or assertion not in final_source
                or not re.search(rb'\bassert\s*\(', assertion)
                or re.search(rb'\bassert\s*\(\s*true\s*[,)]', assertion)
                or not any(check.get('status') == 'pass' and check.get('rc') == 0
                    and check.get('command') == ['lua5.1', '-e', PRELUDE, Path(evidence['path']).name]
                    for check in result['checks'])):
            raise ValueError('A reused assertion, focused command or frozen receipt changed.')
    return compiled, oracle


def compose(engine, job_id, program, composition, authorization):
    """Retain a host-audited contract without rewriting model preparation history.

    No inference, coding, publication or completion is authorized here. Exact
    replay is read-only; a different contract cannot replace an admitted one.
    """
    if not authorization.strip():
        raise ValueError('Owner authority for composed local verification is required.')
    with write_transaction(engine) as c:
        job = intake.require(c, planning_jobs, job_id)
        payload = job['payload']
        if payload.get('evidence_composition'):
            prior = payload['evidence_composition']
            if prior['composition'] != composition or prior['program'] != program:
                raise ValueError('An admitted composed contract cannot be replaced.')
            composed_checks(c, job)
            return prior
        approval, permission = feature_steps.parent(c, payload['feature_decision_id'])
        from workbench.feature_check_preparation import finished_history
        if (job['kind'] != 'feature_execution' or job['status'] not in {'failed', 'queued'}
                or payload['phase'] not in {'held', 'prepare_independent_checks'}
                or payload['current_step_scope']['scope']['role'] != 'verification'
                or payload.get('completed_checks') or payload.get('feature_verification_claim')
                or not finished_history(c, job, payload['independent_check_preparation']['history'])
                or c.execute(select(model_attempts.c.id).where(model_attempts.c.finished_at.is_(None))).first()
                or c.execute(select(requests.c.id).where(requests.c.status.in_(
                    ['pending', 'running', 'checking', 'cancel_requested']))).first()):
            raise ValueError('Composed checks require settled preparation and no active or completed verification.')
        value = dict(program=program, composition=composition,
            authorization_sha256=sha(authorization.encode()), recorded_at=intake.now(),
            scope_binding=payload['current_step_scope']['scope_binding'],
            scope=payload['current_step_scope'],
            history_binding=digest(payload['independent_check_preparation']),
            records_binding=digest(payload['completed_steps']))
        receipt = {**value, 'binding':digest(value)}
        updated = {**payload, 'evidence_composition':receipt, 'phase':'independent_checks_ready'}
        composed_checks(c, {**job, 'payload':updated})
        feature_steps.validate_scope(c, payload['current_step_scope'])
        c.execute(planning_jobs.update().where(planning_jobs.c.id == job_id).values(
            status='queued', finished_at=None, payload=updated,
            message='Audited existing evidence and supplemental observations are ready for combined local execution. Earlier failures remain retained.'))
        c.execute(task_sessions.update().where(task_sessions.c.id == approval['id']).values(
            status='pending', finished_at=None, message='Combined local checks are prepared; the feature is not yet accepted.'))
        return receipt


def retry_composed_timeout(engine, job_id, authorization):
    """Re-admit the same local contract after a positively settled suite timeout.

    Keep the failed grant/outcome, count the consumed attempt, and do not renew
    time, inference, source, scope or publication permission. This cannot replay
    an uncertain operation or use a candidate exposed by an unsuccessful run.
    """
    from workbench.repair_continuation import private_json
    with write_transaction(engine) as c:
        job = intake.require(c, planning_jobs, job_id)
        payload = job['payload']
        approval, permission = feature_steps.parent(c, payload['feature_decision_id'])
        if not authorization.strip() or not payload.get('evidence_composition'):
            raise ValueError('Exact owner authority and the same composed contract are required.')
        if payload.get('phase') == 'independent_checks_ready' and payload.get('verification_history'):
            return payload['verification_history'][-1]
        root = Path(payload['feature_verification_claim']['root'])
        runtime = Runtime(root)
        grant = c.execute(select(grants).where(grants.c.snapshot['recovery']['root'].as_string()==str(root))).mappings().one()
        row = c.execute(select(requests).where(requests.c.grant_id==grant['id'])).mappings().one()
        spec = runtime.spec
        marker = private_json(root/('ownership-'+row['id']+'.json'))
        owned_row = {**row,'result':{**row['result'],'owner':marker['owner']}}
        failure = runtime.recover_outcome(owned_row, grant)
        usage_bytes = (root/'usage.jsonl').read_bytes()
        usage = [json.loads(line) for line in usage_bytes.decode().splitlines()]
        if (job['status'] != 'failed' or payload['phase'] != 'held'
                or row['status'] != 'failed' or not row['finished_at'] or not grant['revoked_at']
                or not failure or failure['result'] != row['result'] or not runtime.cleanup(owned_row)
                or spec['independent_checks']['evidence_composition'] != payload['evidence_composition']['composition']
                or spec.get('attempt',1) >= permission['budget']['verification_attempts']
                or (root/'combined.patch').exists() or tree(runtime.workspace) != json.loads(runtime.initial.read_text())
                or len(usage) != 1 or usage[0].get('status') != 'timeout'
                or usage[0].get('cgroup_removed') is not True
                or any(usage[0].get(k) for k in ('oom_kill','pids_limit_hit','leaked_after_kill'))
                or c.execute(select(requests.c.id).where(requests.c.status.in_(
                    ['pending','running','checking','cancel_requested']))).first()
                or c.execute(select(model_attempts.c.id).where(model_attempts.c.finished_at.is_(None))).first()):
            raise ValueError('Only a settled original-suite timeout within the existing attempt limit may be retried.')
        composed_checks(c, job)
        record = dict(execution_id=row['id'],grant_binding=grant['binding'],result_binding=digest(row['result']),
            specification_binding=spec['binding'],attempt=spec.get('attempt',1),
            original_only=True,failed_changes_reused=False,usage=None,local_timeout=True,
            resource_receipt_sha256=sha(usage_bytes),authorization_sha256=sha(authorization.encode()))
        updated = {**payload,'phase':'independent_checks_ready',
            'feature_verification_attempt':spec.get('attempt',1)+1,
            'verification_history':payload.get('verification_history',[])+[record],
            'current_step_scope':payload['evidence_composition']['scope']}
        updated.pop('feature_verification_claim')
        c.execute(planning_jobs.update().where(planning_jobs.c.id==job_id).values(
            status='queued',finished_at=None,payload=updated,
            message='The original suite watchdog expired. Its settled failure is retained; the same local checks may run within the original limits.'))
        c.execute(task_sessions.update().where(task_sessions.c.id==approval['id']).values(
            status='pending',finished_at=None,message='Retrying the same local combined verification; no model request or permission expansion.'))
        return record


class Runtime(CodingRuntime):
    """Fixed local checks only; deliberately does not load a coding recipe/key."""

    operation_kind = KIND

    def __init__(self, root, stop_event=None):
        from workbench.repair_continuation import private_json

        self.root = owned(Path(root))
        self.stop_event = stop_event
        self.spec = private_json(self.root / "feature-verification.json", 262144)
        spec = self.spec
        if spec["operation_kind"] != KIND or spec["binding"] != digest(
            {k: v for k, v in spec.items() if k != "binding"}
        ):
            raise ValueError("Combined verification configuration changed.")
        compiled, oracle = feature_checks.compile(
            spec["independent_packet"], spec["independent_checks"]["program"], retained=True,
            composition=spec['independent_checks'].get('evidence_composition')
        )
        if (
            compiled != spec["independent_checks"]
            or (self.root / "recipe-oracle.lua").read_text() != oracle
        ):
            raise ValueError("Independent feature oracle changed.")
        location = self.root / "workspace-location.json"
        self.workspace = (
            Path(private_json(location)["path"])
            if location.exists()
            else self.root / "unprepared-workspace"
        )
        if location.exists() and (
            self.workspace.is_symlink()
            or self.workspace.name != "workspace"
            or not self.workspace.parent.name.startswith("workbench-verify-")
            or self.workspace.parent.stat().st_uid != os.getuid()
        ):
            raise ValueError("Combined verification workspace ownership changed.")
        self.source, self.base = spec["original_source"], spec["original_base"]
        self.paths = tuple(spec["paths"])
        self.initial = self.root / "base-manifest.json"
        self.oracle_source = self.root / "recipe-oracle.lua"
        self.package = None
        self.recipe = {"operation_kind": KIND}  # Only shared fixed local check mechanics.
        self.deadline = time.monotonic() + 2400
        self.grant_deadline = datetime.fromisoformat(spec["expires_at"])
        self.execution_contract = dict(
            operation_kind=KIND,
            repository="Twich-Team/TwichUI",
            task_id=spec["verification_task_id"],
            title="Verify the complete feature",
            source=self.source,
            base=self.base,
            paths=list(self.paths),
            provider="none",
            model=None,
            inference_allowed=False,
            edit_tools_allowed=False,
            publication_allowed=False,
            fallback_allowed=False,
            limits=dict(checks_seconds=2400, patch_bytes=393216, changed_lines=900, paths=18),
        )

    def identity(self):
        return dict(
            operation_kind=KIND,
            owner_uid=os.getuid(),
            workspace=str(self.workspace),
            specification_sha256=sha((self.root / "feature-verification.json").read_bytes()),
            oracle_sha256=sha(self.oracle_source.read_bytes()),
            base_manifest_sha256=sha(self.initial.read_bytes()) if self.initial.exists() else None,
            helper_sha256={
                name: sha(Path(__file__).with_name(name).read_bytes())
                for name in (
                    "feature_verification.py",
                    "feature_checks.py",
                    "feature_source.py",
                    "feature_steps.py",
                    "feature_permission.py",
                    "real_handoff.py",
                    "real_execution.py",
                    "real_workspace.py",
                    "real_confine.py",
                    "real_sandbox.py",
                    "verification_process.py",
                    "storage_resources.py",
                    "bug_evidence.py",
                    "bug_program.py",
                    "schemas.py",
                )
            },
        )

    def preflight(self):
        clean = (
            git(Path(self.source), "rev-parse", "HEAD").strip() == self.base
            and not git(Path(self.source), "status", "--porcelain=v1", "--ignored")
            and git(self.workspace, "rev-parse", "HEAD").strip() == self.base
            and tree(self.workspace) == json.loads(self.initial.read_text())
        )
        return [
            dict(
                name="Original base, isolated clean copy and frozen independent observations",
                status="passed" if clean else "blocked",
            )
        ]

    def _check(self, argv, cwd, *, loopback=False):
        if self.stop_event is not None and self.stop_event.is_set():
            raise ValueError("Combined feature checks were stopped before the next command.")
        result = self.check(
            argv, cwd, ro=[self.oracle_source],
            timeout=600 if argv == ['bash', 'tests/run.sh'] else 120, private_loopback=loopback
        )
        self._check_index = getattr(self, "_check_index", 0) + 1
        durable(
            self.root / "local-checks" / f"{self._check_index:02}.json",
            json.dumps(result, sort_keys=True).encode(),
        )
        require_safe_check(result)
        return result

    def expected_checks(self):
        focused = self.spec["independent_checks"]["focused_tests"]
        return (
            [["luac5.1", "-p", p] for p in self.paths]
            + [["lua5.1", "-e", PRELUDE, Path(p).name] for p in focused]
            + [["lua5.1", "-e", PRELUDE, str(self.oracle_source)], ["bash", "tests/run.sh"]]
        )

    def original_valid(self, original):
        if len(original) != 2:
            return False
        observed = original[1].get("out", "")
        phases = self.spec["independent_checks"]["program"]["phases"]
        marks = re.findall(
            r"^WB_OBSERVATION feature-integration ([0-9]+) (expected|incorrect)$",
            observed,
            re.MULTILINE,
        )
        expected = [
            (str(i + 1), "expected" if p["expected"] == p["incorrect"] else "incorrect")
            for i, p in enumerate(phases)
        ]
        return bool(
            suite_inventory(original[0], self.expected_suite())
            and original[1].get("command") == self.expected_checks()[-2]
            and original[1].get("rc") == 1
            and original[1].get("status") == "fail"
            and marks == expected
            and re.findall(r"^WB_BEHAVIOR feature-integration (PASS|FAIL)$", observed, re.MULTILINE)
            == ["FAIL"]
            and "WB_FIXTURE_ERROR" not in observed
            and "outside-reviewed-results" not in observed
        )

    def verify_checks(self, result, grant):
        expected = self.expected_checks()
        checks = result.get("checks", [])
        original = result.get("original_checks", [])
        return bool(
            result.get("operation_kind") == KIND
            and result.get("inference_ran") is False
            and result.get("usage") is None
            and result.get("contributions") == self.spec["contributions"]
            and len(checks) == len(expected)
            and all(
                c.get("status") == "pass" and c.get("rc") == 0 and c.get("command") == command
                for c, command in zip(checks, expected, strict=True)
            )
            and suite_inventory(checks[-1], self.expected_suite())
            and self.original_valid(original)
            and all(
                not any(
                    c.get("usage", {}).get(k)
                    for k in ("oom_kill", "pids_limit_hit", "leaked_after_kill")
                )
                and c.get("usage", {}).get("cgroup_removed") is True
                for c in [*checks, *original]
            )
            and result["manifest"]["oracle_sha256"] == sha(self.oracle_source.read_bytes())
            and result["manifest"]["original_manifest_sha256"] == sha(self.initial.read_bytes())
            and result["manifest"]["workflow_task_binding"]
            == digest(grant["snapshot"]["workflow_task"])
        )

    def execute(self, identity, fence, permitted):
        from workbench import real_confine
        from workbench.candidate_review import compare

        if not permitted() or any(g["status"] != "passed" for g in self.preflight()):
            raise ValueError("Combined verification was stopped or its original input changed.")
        durable(
            self.root / ("ownership-" + identity + ".json"),
            json.dumps(dict(owner=owner.owner_identity(), cgroup=real_confine.root())).encode(),
        )
        original = []
        for i, (argv, cwd, loopback) in enumerate(
            (
                (["bash", "tests/run.sh"], self.workspace, True),
                (self.expected_checks()[-2], self.workspace / "tests", False),
            )
        ):
            result = self._check(argv, cwd, loopback=loopback)
            original.append(result)
            durable(
                self.root / ("original-check-" + str(i) + ".json"),
                json.dumps(result, sort_keys=True).encode(),
            )
        if tree(self.workspace) != json.loads(self.initial.read_text()):
            raise ValueError("An original check altered its read-only source.")
        if not self.original_valid(original):
            raise OriginalObservationsUnavailable(
                "Original source did not establish every changed observation and preserved control. Candidate checks did not start."
            )
        source = Path(self.spec["feature_input"]["source"])
        patch = subprocess.check_output(
            [
                "git",
                "-C",
                str(source),
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--binary",
                self.base,
                self.spec["feature_input"]["commit"],
            ],
            env=dict(PATH="/usr/bin:/bin", GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1"),
            timeout=30,
        )
        if not patch or len(patch) > 393216 or not permitted():
            raise ValueError("The combined checked feature patch is absent, oversized or stopped.")
        patch_path = self.root / "combined.patch"
        durable(patch_path, patch)
        git(self.workspace, "apply", "--check", str(patch_path))
        git(self.workspace, "apply", str(patch_path))
        initial = json.loads(self.initial.read_text())
        current = tree(self.workspace)
        changed = sorted(p for p in set(initial) | set(current) if initial.get(p) != current.get(p))
        if (
            not changed
            or any(p not in self.paths or p not in initial or p not in current for p in changed)
            or any((self.workspace / p).is_symlink() for p in changed)
        ):
            raise ValueError("The combined patch differs from the approved checked file union.")
        for line in git(self.workspace, "diff", "--numstat", self.base).splitlines():
            additions, deletions, path = line.split("\t")
            if (
                not additions.isdigit()
                or not deletions.isdigit()
                or path.startswith("tests/")
                and deletions != "0"
            ):
                raise ValueError(
                    "Combined native checks may only add assertions to original tests."
                )
        if (
            sum(
                int(n)
                for line in git(self.workspace, "diff", "--numstat", self.base).splitlines()
                for n in line.split("\t")[:2]
            )
            > 900
        ):
            raise ValueError("The combined feature exceeds its approved changed-line ceiling.")
        checks = []
        for argv in self.expected_checks():
            if not permitted():
                raise ValueError("Combined verification stopped before a check.")
            cwd = self.workspace / "tests" if argv[0] == "lua5.1" else self.workspace
            checks.append(self._check(argv, cwd, loopback=argv == ["bash", "tests/run.sh"]))
        if current != tree(self.workspace) or not permitted():
            raise ValueError(
                "Combined candidate changed or permission was withdrawn during checks."
            )
        files = [
            compare(
                p,
                subprocess.check_output(
                    ["git", "-C", str(self.workspace), "show", self.base + ":" + p]
                ),
                (self.workspace / p).read_bytes(),
                input_limit=65536,
            )
            for p in changed
        ]
        if any(
            f["kind"] != "modified" or f.get("truncated") or f.get("diff") is None for f in files
        ):
            raise ValueError(
                "The complete combined feature cannot be reviewed within its diff bound."
            )
        linked = self.workflow_task
        manifest = dict(
            request_id=identity,
            fence=fence,
            base=self.base,
            tree=current,
            patch_sha256=sha(patch),
            oracle_sha256=sha(self.oracle_source.read_bytes()),
            original_manifest_sha256=sha(self.initial.read_bytes()),
            specification_binding=self.spec["binding"],
            checked_input=self.spec["feature_input"],
            workflow_task_binding=digest(linked),
            workflow_task=dict(intake_id=linked["intake_id"], task_id=linked["task"]["id"]),
        )
        raw = json.dumps(manifest, sort_keys=True).encode()
        candidate = sha(raw)
        result = dict(
            operation_kind=KIND,
            candidate_id=candidate,
            manifest=manifest,
            files=files,
            checks=checks,
            original_checks=original,
            contributions=self.spec["contributions"],
            usage=None,
            inference_ran=False,
            verification="passed"
            if all(c["status"] == "pass" and c["rc"] == 0 for c in checks)
            else "failed",
            authorship=dict(
                kind="checked-feature-steps",
                contributions_binding=digest(self.spec["contributions"]),
            ),
        )
        frozen = self.root / "candidates" / candidate
        durable(frozen / "manifest.json", raw)
        durable(frozen / "changes.patch", patch)
        for path in changed:
            durable(frozen / "files" / path, (self.workspace / path).read_bytes())
        durable(frozen / "result.json", json.dumps(result, sort_keys=True).encode())
        return result

    def verify_frozen(self, result):
        try:
            if not re.fullmatch(r"[a-f0-9]{64}", result["candidate_id"]) or any(
                f["path"] not in self.paths for f in result["files"]
            ):
                return False
            frozen = self.root / "candidates" / result["candidate_id"]
            manifest = (frozen / "manifest.json").read_bytes()
            return (
                sha(manifest) == result["candidate_id"]
                and json.loads(manifest) == result["manifest"]
                and result["manifest"]["specification_binding"] == self.spec["binding"]
                and sha((frozen / "changes.patch").read_bytes())
                == result["manifest"]["patch_sha256"]
                and json.loads((frozen / "result.json").read_text()) == result
                and all(
                    sha((frozen / "files" / f["path"]).read_bytes())
                    == result["manifest"]["tree"][f["path"]]
                    for f in result["files"]
                )
            )
        except OSError, ValueError, KeyError, TypeError:
            return False

    def failure_evidence(self, *args):
        return dict(operation_kind=KIND, inference_ran=False, usage=None)


def authority(c, snapshot, *, active=True):
    from workbench.feature_run_recovery import deadline as parent_deadline
    spec = snapshot["feature_verification"]
    approval, permission, contributions, paths = checked_chain(
        c, spec["decision_id"], spec["records"], spec["feature_input"], active=active
    )
    linked = task_execution.snapshot(
        c, approval["intake_id"], spec["verification_task_id"], approval["id"]
    )
    feature_steps.validate_scope(c, spec["scope"], active=False)
    job = (
        c.execute(
            select(planning_jobs).where(
                planning_jobs.c.kind == "feature_execution",
                planning_jobs.c.payload["feature_decision_id"].as_string() == approval["id"],
            )
        )
        .mappings()
        .one()
    )
    if (
        spec["binding"] != digest({k: v for k, v in spec.items() if k != "binding"})
        or snapshot["workflow_task"] != linked
        or spec["parent_binding"] != approval["binding"]
        or spec["records"] != job["payload"].get("completed_steps")
        or contributions != spec["contributions"]
        or paths != spec["paths"]
        or parent_deadline(
            c,approval,permission,reference=spec.get('time_authority','original-parent-run')) != spec["expires_at"]
        or not 1 <= spec.get("attempt", 1) <= permission["budget"].get("verification_attempts", 1)
        or spec["scope"]["task_id"] != linked["task"]["id"]
        or spec["scope"]["scope"]["role"] != "verification"
        or spec["scope"]["feature_input"] != spec["feature_input"]
    ):
        raise ValueError(
            "The combined operation differs from its actual parent, current checked source or verification task."
        )
    if active and (
        job["status"] != "running" or job["payload"].get("phase") != "checking_combined_feature"
    ):
        raise ValueError("The combined verification owner is no longer current.")
    if spec['independent_checks'].get('evidence_composition'):
        compiled, _ = composed_checks(c, job)
        if (compiled != spec['independent_checks']
                or spec['independent_review'] != dict(kind='audited-evidence-composition',
                    binding=job['payload']['evidence_composition']['binding'])):
            raise ValueError('The composed acceptance contract differs from the frozen verification.')
    return approval, permission, job


def issue(engine, authorization, provenance, runtime, *, task=None):
    if not authorization.strip() or not provenance.strip() or runtime.operation_kind != KIND:
        raise ValueError("Exact parent verification authority is required.")
    if any(g["status"] != "passed" for g in runtime.preflight()):
        raise ValueError("Original source and protected check preparation are not ready.")
    spec = runtime.spec
    with engine.connect() as c:
        approval, _ = feature_steps.parent(c, spec["decision_id"])
        linked = task_execution.snapshot(
            c, approval["intake_id"], spec["verification_task_id"], approval["id"]
        )
    if task != dict(
        intake_id=approval["intake_id"],
        task_id=spec["verification_task_id"],
        approval_id=approval["id"],
    ):
        raise ValueError("The local check grant targets a different real task.")
    at = datetime.now(timezone.utc)
    expires = min(at + timedelta(seconds=2400), datetime.fromisoformat(spec["expires_at"]))
    if expires <= at:
        raise ValueError("The parent feature permission expired before combined checks.")
    snapshot = dict(
        operation_kind=KIND,
        contract=runtime.execution_contract,
        owner_instruction=authorization,
        authorization_sha256=sha(authorization.encode()),
        provenance=provenance,
        workflow_task=linked,
        feature_verification=spec,
        runtime=runtime.identity(),
        recovery=runtime.recovery_identity(),
        grant_deadline=spec["expires_at"],
    )
    with write_transaction(engine) as c:
        authority(c, snapshot)
        prior = (
            c.execute(
                select(grants).where(
                    grants.c.snapshot["operation_kind"].as_string() == KIND,
                    grants.c.snapshot["feature_verification"]["decision_id"].as_string()
                    == spec["decision_id"],
                    grants.c.snapshot["feature_verification"]["verification_task_id"].as_string()
                    == spec["verification_task_id"],
                    grants.c.snapshot["feature_verification"]["attempt"].as_integer()
                    == spec.get("attempt", 1),
                )
            )
            .mappings()
            .first()
        )
        if prior:
            if prior["snapshot"] != snapshot:
                raise ValueError(
                    "This combined verification task already owns different immutable inputs."
                )
            return dict(prior)
        row = dict(
            id=str(uuid4()),
            snapshot=snapshot,
            binding=digest(snapshot),
            issued_at=at.isoformat(),
            expires_at=expires.isoformat(),
        )
        c.execute(grants.insert().values(**row))
    return row


def valid(grant):
    try:
        snapshot = grant["snapshot"]
        spec = snapshot["feature_verification"]
        contract = snapshot["contract"]
        return bool(
            is_grant(grant)
            and not grant["revoked_at"]
            and digest(snapshot) == grant["binding"]
            and spec["binding"] == digest({k: v for k, v in spec.items() if k != "binding"})
            and contract["operation_kind"] == KIND
            and contract["provider"] == "none"
            and contract["model"] is None
            and contract["inference_allowed"] is False
            and contract["edit_tools_allowed"] is False
            and contract["fallback_allowed"] is False
            and contract["publication_allowed"] is False
            and contract["base"] == spec["original_base"]
            and contract["source"] == spec["original_source"]
            and contract["paths"] == spec["paths"]
            and contract["task_id"] == spec["verification_task_id"]
            and contract["repository"] == "Twich-Team/TwichUI"
            and contract["limits"]
            == dict(checks_seconds=2400, patch_bytes=393216, changed_lines=900, paths=18)
            and datetime.now(timezone.utc)
            < datetime.fromisoformat(grant["expires_at"])
            <= datetime.fromisoformat(grant["issued_at"]) + timedelta(seconds=2400)
            and datetime.fromisoformat(grant["expires_at"])
            <= datetime.fromisoformat(spec["expires_at"])
        )
    except ValueError, KeyError, TypeError:
        return False


def checked(c, row, grant, *, active=True):
    """No fabricated model ledger for checks; all actual child ledgers still gate."""
    from workbench.result_rejection import later_review
    from workbench.task_cleanup import require_current

    if active:
        require_current(c, grant["snapshot"]["workflow_task"]["intake_id"])

    if (
        not is_grant(grant)
        or grant["binding"] != digest(grant["snapshot"])
        or row["status"] != "checked"
        or not row["finished_at"]
        or row["result"].get("verification") != "passed"
        or row["result"].get("operation_kind") != KIND
        or row["result"].get("inference_ran") is not False
        or row["result"].get("usage") is not None
        or c.execute(
            select(model_attempts.c.id).where(model_attempts.c.execution_id == row["id"])
        ).first()
        or later_review(c.connection.driver_connection, row["id"])
    ):
        raise ValueError(
            "An actual combined local verification result is required; model usage cannot be fabricated."
        )
    authority(c, grant["snapshot"], active=False)
    runtime = Runtime(grant["snapshot"]["recovery"]["root"])
    proof = runtime.recover_completion(row, grant)
    if not proof or proof["result"] != row["result"]:
        raise ValueError("The exact combined completion or original/candidate checks changed.")
    return proof["result"]


def process_one(engine, runtime, stop_event=None):
    """The existing execution dispatcher delegates this fixed local operation."""
    owner.recover(engine, runtime)
    with engine.connect() as c:
        rows = c.execute(
            select(requests).where(requests.c.status == "pending").order_by(requests.c.created_at)
        ).mappings()
        pending = grant = None
        for row in rows:
            candidate = (
                c.execute(select(grants).where(grants.c.id == row["grant_id"])).mappings().one()
            )
            if is_grant(candidate) and owner.recovery_matches(runtime, candidate):
                pending, grant = dict(row), dict(candidate)
                break
    if pending is None:
        return False
    try:
        with engine.connect() as c:
            authority(c, grant["snapshot"])
        ready = (
            valid(grant)
            and runtime.identity() == grant["snapshot"]["runtime"]
            and all(gate["status"] == "passed" for gate in runtime.preflight())
        )
    except ValueError, OSError, HTTPException, KeyError, TypeError:
        ready = False
    with write_transaction(engine) as c:
        row = c.execute(select(requests).where(requests.c.id == pending["id"])).mappings().one()
        actual = c.execute(select(grants).where(grants.c.id == row["grant_id"])).mappings().one()
        if row["status"] != "pending":
            return False
        if not ready or not valid(actual):
            c.execute(
                requests.update()
                .where(requests.c.id == row["id"])
                .values(
                    status="blocked",
                    finished_at=intake.now(),
                    result=dict(operation_kind=KIND, inference_ran=False, usage=None),
                    message="Combined check permission or original inputs changed. No checks or model started.",
                )
            )
            return True
        authority(c, actual["snapshot"])
        if (
            c.execute(
                select(requests.c.id).where(
                    requests.c.status.in_(["running", "checking", "cancel_requested"])
                )
            ).first()
            or c.execute(
                select(model_attempts.c.id).where(model_attempts.c.finished_at.is_(None))
            ).first()
        ):
            return False
        fence = str(uuid4())
        actor = owner.owner_identity()
        c.execute(
            requests.update()
            .where(requests.c.id == row["id"])
            .values(
                status="checking",
                fence=fence,
                started_at=intake.now(),
                result=dict(owner=actor, operation_kind=KIND, inference_ran=False, usage=None),
                message="Checking the complete feature against original behavior and preserved defaults. No model is running.",
            )
        )
    runtime.workflow_task = grant["snapshot"]["workflow_task"]

    def permitted():
        if stop_event is not None and stop_event.is_set():
            return False
        try:
            with engine.connect() as c:
                g = c.execute(select(grants).where(grants.c.id == grant["id"])).mappings().one()
                r = (
                    c.execute(select(requests).where(requests.c.id == pending["id"]))
                    .mappings()
                    .one()
                )
                authority(c, g["snapshot"])
                return valid(g) and r["fence"] == fence and r["status"] == "checking"
        except ValueError, OSError, HTTPException, KeyError, TypeError:
            return False

    try:
        result = runtime.execute(pending["id"], fence, permitted)
        if (
            not runtime.verify_frozen(result)
            or not permitted()
            or not runtime.verify_checks(result, grant)
        ):
            raise ValueError(
                "The exact original controls or combined frozen candidate checks did not qualify."
            )
        status = "checked"
        runtime.record_completion(grant, pending["id"], fence, result, intake.now())
    except Exception as error:
        result = dict(
            operation_kind=KIND,
            inference_ran=False,
            usage=None,
            verification="not_completed",
            failure_kind="original_observations_not_qualified"
            if isinstance(error, OriginalObservationsUnavailable)
            else "combined_checks_not_qualified",
            error=(type(error).__name__ + ": " + str(error))[:1000],
        )
        if isinstance(error, OriginalObservationsUnavailable):
            result["original_check_files"] = [
                dict(
                    path="original-check-" + str(i) + ".json",
                    sha256=sha(
                        (runtime.root / ("original-check-" + str(i) + ".json")).read_bytes()
                    ),
                )
                for i in range(2)
            ]
        status = "failed"
        runtime.record_outcome(grant, pending["id"], fence, result, intake.now(), actor)
    with engine.connect() as c:
        current = c.execute(select(requests).where(requests.c.id == pending["id"])).mappings().one()
    if current["fence"] != fence:
        return True
    if current["status"] == "cancel_requested":
        owner.settle_cancellation(engine, runtime, pending["id"], fence, result)
        return True
    if not owner.cleanup_confirmed(runtime, current):
        owner.hold_cleanup(engine, current, result)
        return True
    with write_transaction(engine) as c:
        current = c.execute(select(requests).where(requests.c.id == pending["id"])).mappings().one()
        if current["fence"] != fence or current["status"] != "checking":
            return True
        at = intake.now()
        c.execute(
            requests.update()
            .where(requests.c.id == pending["id"])
            .values(
                status=status,
                finished_at=at,
                result=result,
                message="The complete feature passed its independent offline and repository checks. Nothing was published."
                if status == "checked"
                else "Combined feature checks did not qualify. Earlier work and exact evidence are retained.",
            )
        )
        c.execute(grants.update().where(grants.c.id == grant["id"]).values(revoked_at=at))
    return True


def satisfied_tasks(c, decision_id, feature_input):
    job = (
        c.execute(
            select(planning_jobs).where(
                planning_jobs.c.kind == "feature_execution",
                planning_jobs.c.payload["feature_decision_id"].as_string() == decision_id,
            )
        )
        .mappings()
        .first()
    )
    result = set()
    for saved in job["payload"].get("completed_checks", []) if job else []:
        row = (
            c.execute(select(requests).where(requests.c.id == saved["execution_id"]))
            .mappings()
            .one()
        )
        grant = c.execute(select(grants).where(grants.c.id == row["grant_id"])).mappings().one()
        if (
            not is_grant(grant)
            or grant["snapshot"]["feature_verification"]["feature_input"] != feature_input
            or grant["snapshot"]["feature_verification"]["verification_task_id"] != saved["task_id"]
        ):
            raise ValueError(
                "A completed feature check does not cover the current complete source."
            )
        proof = checked(c, row, grant)
        if proof["candidate_id"] != saved["candidate_id"]:
            raise ValueError("The recorded combined verification candidate changed.")
        result.add(saved["task_id"])
    return result


def advance(engine, job, *, stop_event=None):
    from workbench.feature_preparation import ParentStop
    from workbench.feature_execution import _hold

    fresh = job["payload"]["phase"] == "independent_checks_ready"
    if fresh:
        with engine.connect() as c:
            spec, oracle = specification(c, job)
        root = (
            Path(configuration(engine)["output_root"])
            / "feature-verification"
            / spec["decision_id"]
            / digest(spec["verification_task_id"])[:24]
            / f"attempt-{spec.get('attempt', 1):02}"
        )
        if root.exists():
            raise ValueError(
                "Combined verification artifacts exist without their original claim. Nothing was overwritten or replayed."
            )
        claim = dict(
            owner=owner.owner_identity(), root=str(root), specification_binding=spec["binding"]
        )
        with write_transaction(engine) as c:
            current = intake.require(c, planning_jobs, job["id"])
            feature_steps.parent(c, spec["decision_id"])
            if current["status"] != "queued" or current["payload"] != job["payload"]:
                return False
            c.execute(
                planning_jobs.update()
                .where(planning_jobs.c.id == job["id"])
                .values(
                    status="running",
                    payload={
                        **current["payload"],
                        "phase": "checking_combined_feature",
                        "feature_verification_claim": claim,
                    },
                    message="Checking original behavior and the complete combined feature. No model is running.",
                )
            )
            c.execute(
                task_sessions.update()
                .where(task_sessions.c.id == spec["decision_id"])
                .values(
                    status="active",
                    finished_at=None,
                    message="Checking the complete feature in an isolated local copy.",
                )
            )
        root.mkdir(parents=True, mode=0o700)
        durable(root / "feature-verification.json", json.dumps(spec, sort_keys=True).encode())
        (root / "feature-verification.json").chmod(0o600)
        durable(root / "recipe-oracle.lua", oracle.encode())
        runtime = Runtime(root, ParentStop(engine, spec["decision_id"], stop_event))
        runtime.prepare()
        ref = dict(
            intake_id=job["intake_id"],
            task_id=spec["verification_task_id"],
            approval_id=spec["decision_id"],
        )
        grant = owner.issue(
            engine,
            "Run independent combined feature checks " + spec["binding"],
            "Existing parent feature permission; local checks only, no inference or edit tools.",
            runtime,
            task=ref,
        )
        owner.submit(engine, grant["id"], task_execution.request_id(grant["id"]))
        owner.process_one(engine, runtime=runtime, stop_event=runtime.stop_event)
    else:
        claim = job["payload"]["feature_verification_claim"]
        root = Path(claim["root"])
        runtime = Runtime(
            root, ParentStop(engine, job["payload"]["feature_decision_id"], stop_event)
        )
        spec = runtime.spec
        if spec["binding"] != claim["specification_binding"]:
            raise ValueError("The original combined-check claim changed.")
        owner.recover(engine, runtime)
    with engine.connect() as c:
        grant = (
            c.execute(
                select(grants).where(
                    grants.c.snapshot["operation_kind"].as_string() == KIND,
                    grants.c.snapshot["recovery"]["root"].as_string() == str(root),
                )
            )
            .mappings()
            .first()
        )
        row = (
            c.execute(select(requests).where(requests.c.grant_id == grant["id"])).mappings().first()
            if grant
            else None
        )
        if not row or row["status"] in {"pending", "running", "checking", "cancel_requested"}:
            if not owner.alive(claim["owner"]) and not row:
                return _hold(
                    engine,
                    job,
                    "Combined-check preparation was interrupted before dispatch. Its exact artifacts remain; no model or checks were replayed.",
                )
            return False
        if row["status"] == "checked":
            result = checked(c, row, grant)
            record = dict(
                task_id=spec["verification_task_id"],
                execution_id=row["id"],
                candidate_id=result["candidate_id"],
            )
        else:
            record = None
    with write_transaction(engine) as c:
        current = intake.require(c, planning_jobs, job["id"])
        parent = intake.require(c, task_sessions, spec["decision_id"])
        if (
            current["status"] != "running"
            or current["payload"].get("feature_verification_claim") != claim
        ):
            return False
        if not grant["revoked_at"]:
            c.execute(
                grants.update().where(grants.c.id == grant["id"]).values(revoked_at=intake.now())
            )
        stopped = bool(parent["state"].get("cancel_requested"))
        if not record and not stopped:
            corrected = correct_original_observations(c, current, parent, row, grant, runtime)
            if corrected:
                return True
        ready = bool(record and not stopped)
        payload = {**current["payload"], "phase": "prepare_scopes" if ready else "held"}
        if ready:
            payload["completed_checks"] = current["payload"].get("completed_checks", []) + [record]
            payload["final_verification_execution"] = row["id"]
            payload.pop("current_step_scope", None)
        c.execute(
            planning_jobs.update()
            .where(planning_jobs.c.id == job["id"])
            .values(
                status="queued" if ready else "failed",
                finished_at=None if ready else intake.now(),
                payload=payload,
                message="The complete feature passed independent offline checks. Preparing its coherent delivery result."
                if ready
                else "Combined checks did not finish successfully. Exact original/candidate evidence and earlier work remain saved.",
            )
        )
        c.execute(
            task_sessions.update()
            .where(task_sessions.c.id == parent["id"])
            .values(
                status="pending" if ready else "cancelled" if stopped else "held",
                finished_at=None if ready else intake.now(),
                state={
                    **parent["state"],
                    "feature_verification_result": dict(
                        execution_id=row["id"], status=row["status"]
                    ),
                },
                message="The complete feature passed independent offline checks; delivery remains."
                if ready
                else "Feature stopped. Its original work and combined-check evidence are saved."
                if stopped
                else "Combined checks need attention. Earlier work and exact evidence remain; the feature is not finished.",
            )
        )
    return True


def recover_owned(engine):
    """Finish only an already claimed local-check operation, even while paused.

    This path cannot allocate a grant, submit a request, run checks or call a
    model. Native completion/cleanup remain mandatory; partial work never replays.
    """
    with engine.connect() as c:
        jobs = list(
            c.execute(
                select(planning_jobs).where(
                    planning_jobs.c.kind == "feature_execution",
                    planning_jobs.c.status == "running",
                    planning_jobs.c.payload["phase"].as_string() == "checking_combined_feature",
                )
            ).mappings()
        )
    for job in jobs:
        try:
            claim = job['payload']['feature_verification_claim']
            if not owner.alive(claim['owner']):
                with write_transaction(engine) as c:
                    current = intake.require(c, planning_jobs, job['id'])
                    grant = c.execute(select(grants).where(
                        grants.c.snapshot['operation_kind'].as_string() == KIND,
                        grants.c.snapshot['recovery']['root'].as_string() == claim['root'],
                    )).mappings().first()
                    request = c.execute(select(requests).where(requests.c.grant_id == grant['id'])).mappings().first() if grant else None
                    undispatched = not request or (request['status'] == 'pending'
                        and not request['fence'] and not request['started_at'] and not request['result'])
                    if current['payload'] != job['payload'] or current['status'] != 'running':
                        continue
                    if undispatched:
                        if grant:
                            c.execute(grants.update().where(grants.c.id == grant['id']).values(revoked_at=intake.now()))
                        if request:
                            c.execute(requests.update().where(requests.c.id == request['id']).values(
                                status='blocked',finished_at=intake.now(),
                                message='Local verification owner stopped before dispatch. Nothing was replayed.'))
                if undispatched:
                    from workbench.feature_execution import _hold
                    return _hold(engine, dict(job),
                        'Local verification stopped before dispatch. Earlier checked steps and partial preparation remain saved; nothing was replayed.')
            if advance(engine, dict(job)):
                return True
        except ValueError, OSError, HTTPException, KeyError, TypeError:
            # A live owner's incomplete files are never recovered or overwritten.
            continue
    return False


def observation_binding(program):
    return digest(
        dict(
            setup=program["setup"],
            read_paths=program["read_paths"],
            phases=[
                {k: p[k] for k in ("code", "expected", "incorrect")} for p in program["phases"]
            ],
        )
    )


def correct_original_observations(c, job, parent, row, grant, runtime):
    """Correct only a settled original-only failure; candidate failures never feed this producer."""
    from workbench.repair_continuation import private_json
    from workbench.feature_check_preparation import request_limit, finished_history

    spec = runtime.spec
    try:
        _, permission = feature_steps.parent(c, spec["decision_id"])
        saved = job["payload"]["independent_check_preparation"]
        if (
            row["status"] != "failed"
            or not row["finished_at"]
            or not grant["revoked_at"]
            or row["result"].get("failure_kind") != "original_observations_not_qualified"
            or row["result"].get("operation_kind") != KIND
            or row["result"].get("inference_ran") is not False
            or row["result"].get("usage") is not None
            or parent["state"].get("cancel_requested")
            or spec.get("attempt", 1) >= permission["budget"].get("verification_attempts", 1)
            or len(saved['history']) >= request_limit(c, job, permission)
            or not finished_history(c, job, saved['history'])
            or c.execute(
                select(requests.c.id).where(
                    requests.c.status.in_(["pending", "running", "checking", "cancel_requested"])
                )
            ).first()
            or c.execute(
                select(model_attempts.c.id).where(model_attempts.c.finished_at.is_(None))
            ).first()
            or c.execute(
                select(model_attempts.c.id).where(model_attempts.c.execution_id == row["id"])
            ).first()
            or (runtime.root / "combined.patch").exists()
            or tree(runtime.workspace) != json.loads(runtime.initial.read_text())
        ):
            return False
        marker = private_json(runtime.root / ("ownership-" + row["id"] + ".json"))
        owned = {**row, "result": {**row["result"], "owner": marker["owner"]}}
        failed = runtime.recover_outcome(owned, grant)
        if not failed or failed["result"] != row["result"] or not runtime.cleanup(owned):
            return False
        original = []
        for i, item in enumerate(row["result"]["original_check_files"]):
            expected = "original-check-" + str(i) + ".json"
            if (
                item["path"] != expected
                or sha((runtime.root / expected).read_bytes()) != item["sha256"]
            ):
                return False
            original.append(private_json(runtime.root / expected))
        if len(original) != 2:
            return False
        for result in original:
            require_safe_check(result)
        rejected = observation_binding(saved["proposed_program"]["program"])
        preparation = {
            **saved,
            "feedback": dict(
                kind="original-only-observation-feedback",
                diagnostic=original[1].get("out", "")[-5000:],
                previous_program=saved["proposed_program"]["program"],
                note="Actual unchanged original-source feedback only. No candidate code, coding response or candidate check result was disclosed. Preserve the complete approved requirements and correct the observations.",
            ),
            "rejected_observations": saved.get("rejected_observations", []) + [rejected],
        }
        preparation.pop("proposed_program", None)
        preparation.pop("review", None)
        record = dict(
            execution_id=row["id"],
            grant_binding=grant["binding"],
            result_binding=digest(row["result"]),
            specification_binding=spec["binding"],
            attempt=spec.get("attempt", 1),
            original_only=True,
            failed_changes_reused=False,
            usage=None,
        )
        payload = {
            **job["payload"],
            "phase": "prepare_independent_checks",
            "feature_verification_attempt": spec.get("attempt", 1) + 1,
            "independent_check_preparation": preparation,
            "verification_history": job["payload"].get("verification_history", []) + [record],
        }
        payload.pop("feature_verification_claim", None)
        c.execute(
            planning_jobs.update()
            .where(planning_jobs.c.id == job["id"])
            .values(
                status="queued",
                finished_at=None,
                payload=payload,
                message="The original-source observations did not qualify. Correcting them within the same feature limits; implementation changes remain withheld from the check producer.",
            )
        )
        c.execute(
            task_sessions.update()
            .where(task_sessions.c.id == parent["id"])
            .values(
                status="pending",
                finished_at=None,
                message="Correcting independent observations using original-source feedback. Earlier implementation work and usage remain saved.",
            )
        )
        return True
    except ValueError, OSError, HTTPException, KeyError, TypeError:
        return False
