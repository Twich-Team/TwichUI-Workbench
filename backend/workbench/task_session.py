"""One bounded session provisioner used by the existing worker, disabled by default.

Reuses Runtime, frozen controls, existing lease controller, worker recovery and PR
owner. No task approval, model retry, recurring scheduler or parallel engine.
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from sqlalchemy import select
from workbench import real_execution as execution, task_budget, task_execution, real_publication
from workbench.db import proposal_decisions, write_transaction, real_publications
from workbench.real_workspace import Runtime, durable, sha, PRELUDE, suite_inventory

ROOT = Path(__file__).resolve().parents[2]


def delegated_command(options, command):
    """Pass only the owned execution inputs into a fresh user service."""
    inputs = ("WORKBENCH_RESEARCH_ROOT", "WORKBENCH_PREPARATION_CONFIG", "TMPDIR")
    from workbench import everyday
    if everyday.enabled():
        inputs += ("WORKBENCH_PLANNER", "WORKBENCH_EVERYDAY_CONFIG", "WORKBENCH_CODEX_SOCKET")
        options = [*options, '--property=IPAddressDeny=any', '--property=IPAddressAllow=localhost']
    return [
        "/usr/bin/systemd-run", "--expand-environment=no", *options,
        "/usr/bin/env", "-i", "PATH=/usr/bin:/bin", "LANG=C.UTF-8", f"HOME={Path.home()}",
        "PYTHONDONTWRITEBYTECODE=1",
        *(f"{name}={os.environ[name]}" for name in inputs if name in os.environ),
        *command,
    ]


def read_config(path, *, allow_expired=False):
    raw = Path(path)
    if (
        raw.is_symlink()
        or not raw.is_file()
        or raw.stat().st_uid != os.getuid()
        or raw.stat().st_mode & 0o077
        or raw.stat().st_size > 8192
    ):
        raise ValueError("Private owned one-session configuration required")
    value = json.loads(raw.read_text())
    authored = value.get("authoring_mode") == "explicit-current-session"
    started = datetime.fromisoformat(value["started_at"])
    end = datetime.fromisoformat(value["deadline"])
    if (
        started.utcoffset() is None
        or end.utcoffset() is None
        or not (
            0 < (end - started).total_seconds() <= 10800 or (end - started).total_seconds() == 21600
        )
        or not (
            started <= datetime.now(timezone.utc)
            and (allow_expired or datetime.now(timezone.utc) < end)
        )
        or value.get("max_attempts") != 1
        or value.get("max_probes") != (0 if authored else 1)
        or value.get("max_leases") != (0 if authored else 1)
    ):
        raise ValueError("Current single-cycle bounded session required")
    instruction = Path(value["owner_instruction"])
    from workbench.owner_task import instruction as verify_instruction

    verify_instruction(instruction, value["owner_instruction_sha256"])
    run = Path(value["run_root"])
    if (
        run.is_symlink()
        or not ((not authored and run.parent.resolve() == ROOT / "artifacts/runs" and run.name.startswith("task-lease-"))
                or (authored and run.parent.parent.resolve() == ROOT / "artifacts/runs" and run.parent.name.startswith("task-pr-completion-") and run.name.startswith("authored-")))
        or not run.is_dir()
        or run.stat().st_uid != os.getuid()
    ):
        raise ValueError("Unique existing owned task session root required")
    return value


def provision(runtime, template):
    """Actual deterministic baseline/oracle controls BEFORE any model lease/probe.

    Control patches are frozen non-model fixtures, never implementation output.
    They are applied only to disposable sibling clones, never the model workspace.
    """
    runtime.prepare()
    if template.get('independent_program_reference'):
        reference=runtime.root/'independent-program-reference.json'
        durable(reference,json.dumps(template['independent_program_reference'],sort_keys=True).encode())
        reference.chmod(0o600)
    if (getattr(runtime,'recipe',None) or {}).get('repair_cycle'):
        from workbench.repair_cycle import reuse_baseline
        baseline=None if getattr(runtime,'repair_profile_v2',False) else reuse_baseline(runtime,template.get('baseline_source'))
        if baseline is None:
            baseline=runtime.check(runtime.expected_checks()[-1],runtime.workspace,timeout=600,private_loopback=True)
        durable(runtime.root/'fresh-baseline-suite.json',json.dumps(baseline,sort_keys=True).encode())
        if getattr(runtime,'repair_profile_v2',False):
            from workbench.repair_profiles import prepare_original
            prepare_original(runtime,baseline)
        elif not suite_inventory(baseline,runtime.expected_suite()):
            raise ValueError('Existing baseline failed; no repair attempt is ready')
    elif (getattr(runtime, 'recipe', None) or {}).get('verification_profile'):
        from workbench.verification_profiles import prepare,reuse_controls
        if 'scenario_preparation' in template:
            from workbench.regression_evidence import compile_plan, patches
            compiled = compile_plan(template['scenario_preparation'])
            if runtime.oracle_source.read_text() != compiled['oracle']:
                raise ValueError('Generated scenario oracle differs')
            control_patches = patches(runtime, compiled)
        else:
            control_patches = template['control_patches']
        if not reuse_controls(runtime,template.get('control_source'),control_patches):
            prepare(runtime, control_patches)
    else:
        _legacy_controls(runtime, template)
    marker = runtime.root / "private-boundary-marker"
    durable(marker, b"NON-MODEL boundary marker")
    code = (
        "import socket,resource;assert resource.getrlimit(resource.RLIMIT_FSIZE)[1]==16777216;"
        "\ntry:socket.socket();raise AssertionError('network escape')\nexcept PermissionError:pass\n"
        "try:open("
        + repr(str(marker))
        + ");raise AssertionError('read escape')\nexcept PermissionError:pass\nprint('bounded model command boundary verified')"
    )
    boundary = runtime.check(["python3", "-c", code], runtime.workspace)
    if boundary["status"] != "pass" or boundary["rc"] != 0:
        raise ValueError("Actual model command boundary failed")
    proof = runtime.root / "static-boundary.json"
    durable(proof, json.dumps(dict(checks={"model-command": boundary}), sort_keys=True).encode())
    runtime.record_controls(proof)
    durable(
        runtime.root / "preparation-completed.json",
        json.dumps(
            dict(
                base=runtime.base,
                recipe_sha256=sha((runtime.root / "recipe.json").read_bytes()),
                no_generation=True,
            ),
            sort_keys=True,
        ).encode(),
    )


def _legacy_controls(runtime, template):
    baseline = runtime.check(
        ["bash", "tests/run.sh"], runtime.workspace, timeout=600, private_loopback=True
    )
    durable(
        runtime.root / "fresh-baseline-suite.json", json.dumps(baseline, sort_keys=True).encode()
    )
    if not suite_inventory(baseline, runtime.expected_suite()):
        raise ValueError("Complete confined baseline failed")
    original = runtime.workspace
    results = {}
    for name, expected in runtime.recipe["controls"].items():
        parent = Path(tempfile.mkdtemp(prefix="workbench-verify-"))
        try:
            clone = parent / "workspace"
            subprocess.run(
                [
                    "git",
                    "-c",
                    "core.hooksPath=/dev/null",
                    "-c",
                    "protocol.allow=never",
                    "-c",
                    "protocol.file.allow=always",
                    "clone",
                    "--local",
                    "--no-hardlinks",
                    "--quiet",
                    str(original),
                    str(clone),
                ],
                check=True,
                timeout=60,
                env={
                    "PATH": "/usr/bin:/bin",
                    "GIT_CONFIG_GLOBAL": "/dev/null",
                    "GIT_CONFIG_NOSYSTEM": "1",
                },
            )
            runtime.workspace = clone
            if name != "original":
                patch = template["control_patches"][name]
                if not isinstance(patch, str) or len(patch.encode()) > 65536:
                    raise ValueError("Bounded trusted control patch required")
                target = parent / "control.patch"
                target.write_text(patch)
                subprocess.run(
                    ["git", "-c", "core.hooksPath=/dev/null", "apply", str(target)],
                    cwd=clone,
                    check=True,
                    timeout=10,
                )
                runtime.scope()
                durable(runtime.root / "control-fixtures" / (name + ".patch"), patch.encode())
            result = runtime.check(
                ["lua5.1", "-e", PRELUDE, str(runtime.root / "oracle.lua")],
                clone / "tests",
                ro=[runtime.root / "oracle.lua"],
            )
            durable(
                runtime.root / "control-results" / (name + ".json"),
                json.dumps(result, sort_keys=True).encode(),
            )
            if result["status"] != expected or result["rc"] in (None, 97):
                raise ValueError("Independent oracle control did not match its expected outcome")
            results[name] = result
        finally:
            runtime.workspace = original
            shutil.rmtree(parent)
    durable(
        runtime.root / "oracle-controls.json",
        json.dumps(
            dict(oracle_sha256=sha(runtime.oracle_source.read_bytes()), results=results),
            sort_keys=True,
        ).encode(),
    )


def preparation_template(c, linked, budget, engine):
    """Resolve the existing offer owner before loading any per-task template."""
    offered = budget['offer']
    if offered['snapshot'].get('feature_step'):
        selected = offered['snapshot']['feature_step']['scope']
        if selected['decision_id'] != linked['approval']['id'] or selected['task_id'] != linked['task']['id']:
            raise ValueError('Feature preparation belongs to another parent or step.')
        return task_budget.preparation_inputs(c, offered)
    if offered['snapshot'].get('certified_preparation') or offered['snapshot'].get('ready_preparation'):
        selected = offered['snapshot']['reviewed_task']['ref']
        continuation = linked['approval']['payload'].get('execution_continuation', {})
        scope_approval = continuation.get('scope_approval_id', linked['approval']['id'])
        if (selected['intake_id'] != linked['intake_id']
                or selected['task_id'] != linked['task']['id']
                or selected['approval_id'] != scope_approval):
            raise ValueError('Certified offer belongs to another reviewed task')
        return task_budget.preparation_inputs(c, offered)
    from workbench.supported_tasks import load
    template = load(linked['original']['supported_task'], engine)
    return {**template, 'recipe': offered['snapshot']['specification']}


def close_selected(engine, decision_id, *, offer_id=None):
    """Close ONLY this session's execution permission; history/delivery stay intact."""
    with write_transaction(engine) as c:
        query = execution.grants.update()
        if offer_id:
            query = query.where(execution.grants.c.snapshot['task_budget']['offer']['id'].as_string() == offer_id)
        c.execute(
            query
            .where(
                execution.grants.c.snapshot["task_budget"]["decision_id"].as_string()
                == decision_id,
                execution.grants.c.revoked_at.is_(None),
            )
            .values(revoked_at=execution.now())
        )


def finish(engine, config, status, message):
    root = Path(config["run_root"])
    marker = root / "session-finished.json"
    if marker.exists():
        if json.loads(marker.read_text())["task"] != config["task"]:
            raise ValueError("Conflicting session outcome")
        return False
    close_selected(engine, config["task"]["approval_id"], offer_id=config.get('feature_offer_id'))
    if status == "published":
        with engine.connect() as c:
            publication = (
                c.execute(
                    select(real_publications).where(
                        real_publications.c.snapshot["task_budget"]["decision_id"].as_string()
                        == config["task"]["approval_id"]
                    )
                )
                .mappings()
                .one()
            )
        if publication["state"].get("source") == "github":
            from workbench.publication_adapter import PilotGitHub

            client = PilotGitHub(root / "publication", publication["snapshot"])
            client.cleanup(publication["state"]["commit"])
    durable(
        marker,
        json.dumps(
            dict(
                task=config["task"],
                status=status,
                message=message,
                finished_at=execution.now(),
                execution_closed=True,
            ),
            sort_keys=True,
        ).encode(),
    )
    return False


def finished(config_path):
    config = read_config(config_path, allow_expired=True)
    marker = Path(config["run_root"]) / "session-finished.json"
    return (
        marker.is_file()
        and not marker.is_symlink()
        and json.loads(marker.read_text()).get("task") == config["task"]
    )


def process_one(engine, config_path, stop_event=None, prepare=provision, launch=None):
    """Called only by a separately configured one-session worker; never the API."""
    config = read_config(config_path, allow_expired=True)
    if (
        datetime.now(timezone.utc) >= datetime.fromisoformat(config["deadline"])
        and not (Path(config["run_root"]) / "session-claimed.json").exists()
    ):
        return finish(
            engine,
            config,
            "expired",
            "The approved time budget expired before activation. Saved readiness remains; no model starts.",
        )
    ref = config["task"]
    if (Path(config["run_root"]) / "session-finished.json").exists():
        return False
    from workbench import everyday, provider_routes

    codex_route = None
    with engine.connect() as c:
        linked = task_execution.snapshot(c, ref["intake_id"], ref["task_id"], ref["approval_id"])
        approval = (
            c.execute(
                select(proposal_decisions).where(proposal_decisions.c.id == ref["approval_id"])
            )
            .mappings()
            .one()
        )
        budget = task_budget.permission(
            c,
            approval,
            require_current=not (Path(config["run_root"]) / "session-claimed.json").exists(),
            admitting=True,
            task_id=ref['task_id'], offer_id=config.get('feature_offer_id'),
        )
        if everyday.enabled():
            codex_route = provider_routes.run_selection(c, linked)
        if not budget or execution.digest(budget) != config["task_budget_fingerprint"]:
            raise ValueError("Current explicit owner task budget differs from session")
    root = Path(config["run_root"])
    real = root / "real-run"
    real.mkdir(exist_ok=True)
    claim = root / "session-claimed.json"
    if codex_route and codex_route.get('strategy'):
        from workbench.smart_provider import choose
        if not claim.exists():
            from workbench.db import task_sessions as saved_sessions
            with engine.connect() as c:
                state=c.execute(select(saved_sessions.c.state).where(saved_sessions.c.id==ref['approval_id'])).scalar() or {}
            state=state.get('feature_slot_state',state)
            choose(codex_route,lambda:bool(stop_event and stop_event.is_set()),waiting_since=state.get('ai_wait_since',{}))
        else:
            with engine.connect() as c:
                waiting=c.execute(select(execution.requests).join(execution.grants,
                    execution.requests.c.grant_id==execution.grants.c.id).where(
                    execution.grants.c.snapshot['task_budget']['decision_id'].as_string()==ref['approval_id'],
                    execution.requests.c.status=='waiting_ai')).mappings().first()
            if waiting:
                from workbench.smart_coding_wait import resume_controller
                if not resume_controller(engine,root,waiting,config,stop_event,launch):
                    return False
    if not claim.exists():
        # These deterministic proofs are prepared under the same one-session
        # package before any lease, then independently rechecked by the controller.
        proof = Path(config["total_limit_proof"])
        if proof.is_symlink() or not proof.is_file() or proof.stat().st_size > 16384:
            raise ValueError("Bounded installed-sampler proof required before activation")
        if sha(proof.read_bytes()) != config["total_limit_proof_sha256"]:
            raise ValueError("Installed-sampler proof identity changed")
        durable(root / "total-limit-proof.json", proof.read_bytes())
        import runpy

        runpy.run_path(str(ROOT / "scripts/chronicle_lease.py"))["checked_limit"](root)
        from workbench.real_package import package_for

        if config.get('authoring_mode') == 'explicit-current-session':
            from workbench.real_package import Package
            package = Package(root.parent, config['package_baseline_sha256'])
        else:
            package = package_for(root)
        if (
            package is None
            or package.budget.get("evidence_ceiling_bytes") not in (64 << 20, 72 << 20)
            or any(
                package.budget.get(key) not in (1, 2, 3)
                for key in ("max_cycles", "max_probes", "max_attempts")
            )
        ):
            raise ValueError("Append-only one-cycle72MiB package allocation required")
        package.require_time(3000)
        package.enforce_storage(root)
        with engine.connect() as c:
            template = preparation_template(c, linked, budget, engine)
        oracle = template.get("oracle")
        if (
            not isinstance(oracle, str)
            or sha(oracle.encode()) != template["recipe"]["oracle_sha256"]
        ):
            raise ValueError("Frozen supported-task oracle is unavailable")
        durable(real / "recipe.json", json.dumps(template["recipe"], sort_keys=True).encode())
        durable(real / "recipe-oracle.lua", oracle.encode())
        if template.get('repair_resume'):
            durable(real/'repair-resume.json',json.dumps(template['repair_resume'],sort_keys=True).encode())
        if template.get('feature_recovery'):
            durable(real/'feature-recovery.json',json.dumps(template['feature_recovery'],sort_keys=True).encode())
            (real/'feature-recovery.json').chmod(0o600)
        runtime = Runtime(real)
        db = Path(engine.url.database)
        stat = db.stat()
        ready_before = (real / "preparation-completed.json").is_file() and all(
            g["status"] == "passed" for g in runtime.static_preflight()
        )
        if prepare is provision and not ready_before:
            durable(root / "template.json", json.dumps(template, sort_keys=True).encode())
            unit = "workbench-real-prepare-" + root.name
            command = delegated_command([
                "--user",
                "--unit=" + unit,
                "--property=Delegate=yes",
                "--property=MemoryMax=6G",
                "--property=MemorySwapMax=0",
                "--property=TasksMax=128",
                "--property=CPUQuota=200%",
                "--property=RuntimeMaxSec=2400",
                "--property=TimeoutStopSec=15",
                "--property=KillMode=mixed",
                "--wait",
                "--pipe",
            ], [
                str(ROOT / ".venv/bin/python"),
                str(ROOT / "scripts/provision_task.py"),
                str(root),
            ])
            with (root / "preparation-controller.log").open("xb") as log:
                child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                         stdin=subprocess.DEVNULL)
                preparation_end = time.monotonic() + 2420
                while child.poll() is None:
                    stopped = stop_event is not None and stop_event.wait(0.2)
                    if stop_event is None:
                        time.sleep(0.2)
                    expired = (time.monotonic() >= preparation_end
                               or datetime.now(timezone.utc) >= datetime.fromisoformat(config["deadline"]))
                    if (stopped or expired) and child.poll() is None:
                        subprocess.run(["/usr/bin/systemctl", "--user", "stop", unit + ".service"],
                                       stdout=log, stderr=subprocess.STDOUT, check=True, timeout=20)
                        child.wait(timeout=5)
                        return finish(engine, config, "cancelled" if stopped else "expired",
                                      "Stopped during readiness checks. Partial evidence is retained; no model started.")
                if child.returncode:
                    raise subprocess.CalledProcessError(child.returncode, command)
        elif prepare is not provision:
            prepare(runtime, template)
        if (root / "workflow-link.json").exists():
            prior = json.loads((root / "workflow-link.json").read_text())
            if prior["task"] != ref or prior["task_budget_fingerprint"] != execution.digest(budget):
                raise ValueError("Original workflow identity changed")
        else:
            started = time.time()
            link = dict(
                database=str(db),
                database_identity=[stat.st_dev, stat.st_ino],
                task=ref,
                authorization_sha256=config["owner_instruction_sha256"],
                task_budget_fingerprint=execution.digest(budget),
                run_started_at=started,
                run_deadline=min(
                    started + 2400, datetime.fromisoformat(config["deadline"]).timestamp()
                ),
            )
            if config.get('feature_offer_id'):
                link['feature_offer_id'] = config['feature_offer_id']
            if link["run_deadline"] - started != 2400:
                raise ValueError("Full acceptance clock does not fit; no lease")
            durable(root / "workflow-link.json", json.dumps(link).encode())
            (root / "workflow-link.json").chmod(0o600)
        if (
            datetime.now(timezone.utc).timestamp() + 3000
            > datetime.fromisoformat(config["deadline"]).timestamp()
        ):
            raise ValueError("Lease/run/restoration reserve does not fit; no lease")
        if stop_event is not None and stop_event.is_set():
            return finish(
                engine,
                config,
                "cancelled",
                "Stopped before model activation. Readiness evidence is retained.",
            )
        if os.environ.get("WORKBENCH_DEMO_MANIFEST_SHA256"):
            from browser_demo.stage import require_active_stage
            require_active_stage()
        durable(
            claim,
            json.dumps(
                dict(
                    task=ref,
                    task_budget_fingerprint=execution.digest(budget),
                    owner=execution.owner_identity(),
                ),
                sort_keys=True,
            ).encode(),
        )
        # Existing controller owns service/drain/auth/probe/attempt/watchdog/restore.
        command = [
            str(ROOT / ".venv/bin/python"),
            str(ROOT / "scripts/chronicle_lease.py"),
            "controller",
            "--root",
            str(root),
            "--owner-instruction",
            config["owner_instruction"],
        ]
        codex_unit = None
        if codex_route:
            codex_unit = 'workbench-real-codex-' + root.name
            durable(root / 'codex-controller.json', json.dumps({'unit': codex_unit + '.service'}).encode())
            command = delegated_command([
                '--user', '--unit=' + codex_unit, '--property=Delegate=yes',
                '--property=MemoryMax=6G', '--property=MemorySwapMax=0',
                '--property=TasksMax=128', '--property=CPUQuota=200%',
                '--property=RuntimeMaxSec=2400', '--property=TimeoutStopSec=15',
                '--property=KillMode=mixed', '--wait', '--pipe',
            ], [str(ROOT / '.venv/bin/python'), str(ROOT / 'scripts/codex_task.py'), str(root)])
        elif config.get('authoring_mode') == 'explicit-current-session':
            command = ['systemd-run','--user','--unit=workbench-real-authored-'+root.parent.name,
                '--property=WorkingDirectory='+str(ROOT),'--property=Delegate=yes','--property=MemoryMax=6G',
                '--property=MemorySwapMax=0','--property=TasksMax=128','--property=CPUQuota=200%',
                '--property=RuntimeMaxSec=2450','--wait','--pipe',str(ROOT/'.venv/bin/python'),
                str(ROOT/'scripts/complete_authored_task.py'),str(config_path)]

        if launch is None:
            with (root / "session-controller.log").open("xb") as log:
                child = subprocess.Popen(
                    command, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL
                )
                child_proc = Path("/proc") / str(child.pid)
                durable(
                    root / "controller-owner.json",
                    json.dumps(
                        dict(
                            pid=child.pid,
                            boot=Path("/proc/sys/kernel/random/boot_id").read_text(),
                            start=(child_proc / "stat").read_text().split(") ", 1)[1].split()[19],
                        )
                    ).encode(),
                )
                descriptor = os.pidfd_open(child.pid)
                try:
                    while child.poll() is None:
                        if (stop_event is not None and stop_event.is_set()) or datetime.now(
                            timezone.utc
                        ) >= datetime.fromisoformat(config["deadline"]):
                            import signal

                            if codex_unit:
                                subprocess.run(['/usr/bin/systemctl', '--user', 'stop', codex_unit + '.service'],
                                               stdout=log, stderr=subprocess.STDOUT, timeout=20, check=True)
                            else:
                                signal.pidfd_send_signal(descriptor, signal.SIGTERM)
                            child.wait(timeout=225 if everyday.enabled() else 15)
                            break
                        time.sleep(0.1)
                finally:
                    os.close(descriptor)
        else:
            launch(command)
    report_file = root / ("authored-controller-result.json" if config.get("authoring_mode")=="explicit-current-session" else "lease-controller-result.json")
    if not report_file.is_file():
        return False  # Never relaunch a claimed session.
    from workbench import everyday
    reconciled = root / 'lease-restoration-reconciled.json'
    if everyday.enabled() and reconciled.is_file():
        report_file = reconciled
    report = json.loads(report_file.read_text())
    if not report.get("restoration", {}).get("restored"):
        return False  # Restoration uncertainty always wins over result delivery.
    runtime = Runtime(real)
    execution.recover(engine, runtime)  # Recovery only; never process_one/replay.
    with engine.connect() as c:
        row = (
            c.execute(
                select(execution.requests)
                .join(execution.grants, execution.requests.c.grant_id == execution.grants.c.id)
                .where(
                    execution.grants.c.snapshot["task_budget"]["decision_id"].as_string()
                    == ref["approval_id"],
                    execution.grants.c.snapshot['task_budget']['offer']['id'].as_string() == budget['offer']['id'],
                )
            )
            .mappings()
            .first()
        )
    if not row:
        return finish(
            engine,
            config,
            "ended-before-result",
            "Controller ended; no implementation result was recorded. See retained controller evidence.",
        )
    if row["status"] != "checked":
        if row["status"] in {"failed", "blocked", "cancelled", "interrupted"}:
            return finish(
                engine,
                config,
                row["status"],
                "Implementation ended without a qualifying candidate. Its work and usage are saved; a settled scoped repair can be reviewed for continuation.",
            )
        return False  # Existing sealed-outcome recovery still owns pending delivery.
    if config.get("allow_publication") is not True:
        message = "Verified candidate retained; publication not activated."
        if everyday.enabled() and not everyday.clean_workspace(real):
            message += " An owned scratch copy needs attention."
        return finish(engine, config, "verified", message)
    with engine.connect() as c:
        publication = (
            c.execute(
                select(real_publications).where(real_publications.c.execution_id == row["id"])
            )
            .mappings()
            .first()
        )
    if publication and publication["status"] not in real_publication.ACTIVE:
        return finish(engine, config, publication["status"], publication["message"])
    directory = root / "publication"
    directory.mkdir(mode=0o700, exist_ok=True)
    expires = min(
        datetime.fromisoformat(config["deadline"]), datetime.fromisoformat(budget["expires_at"])
    )
    activation = dict(
        decision_id=ref["approval_id"],
        execution_id=row["id"],
        repository=runtime.execution_contract["repository"],
        base=runtime.base,
        max_prs=1,
        draft=True,
        expires_at=expires.isoformat(),
    )
    durable(directory / "activation.json", json.dumps(activation, sort_keys=True).encode())
    (directory / "activation.json").chmod(0o600)
    if everyday.enabled():
        from workbench import draft_delivery
        real_publication.enqueue_one(engine, row["id"])
        return draft_delivery.send(row["id"], stop_event)
    previous = os.environ.get("WORKBENCH_PUBLICATION_ROOT")
    try:
        os.environ["WORKBENCH_PUBLICATION_ROOT"] = str(directory)
        return real_publication.process_one(engine, stop_event=stop_event, execution_id=row["id"])
    finally:
        if previous is None:
            os.environ.pop("WORKBENCH_PUBLICATION_ROOT", None)
        else:
            os.environ["WORKBENCH_PUBLICATION_ROOT"] = previous
