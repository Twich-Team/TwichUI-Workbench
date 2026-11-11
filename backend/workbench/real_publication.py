"""Real-result delivery by the existing worker. Disabled without scoped activation."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import threading
from uuid import UUID, uuid4, uuid5

from fastapi import HTTPException
from sqlalchemy import select, inspect

from workbench import real_execution as execution, task_execution, task_budget
from workbench.db import (
    real_publications as records,
    real_execution_requests as requests,
    real_execution_grants as grants,
    proposal_decisions,
    model_attempts,
    write_transaction,
)
from workbench.visual_scope import digest

ACTIVE = ("pending", "preparing", "pushing", "creating", "reconciling")


class DeliveryHeld(ValueError):
    pass


class UncertainWrite(RuntimeError):
    pass


def _proof(row, grant):
    """Re-read immutable completion; DB flags/model prose cannot certify delivery."""
    from workbench.real_workspace import Runtime

    recovery = grant["snapshot"].get("recovery")
    if not recovery:
        raise DeliveryHeld("The original owned evidence directory is required.")
    runtime = Runtime(Path(recovery["root"]))
    if not runtime.owns_recovery(grant):
        raise DeliveryHeld("The evidence directory identity changed.")
    completed = runtime.recover_completion(row, grant)
    if not completed or digest(completed["result"]) != digest(row["result"]):
        raise DeliveryHeld(
            "The frozen candidate or independent completion evidence failed integrity checks."
        )
    return completed["result"]


def _checked(c, row, grant):
    from workbench.session_authorship import usage_eligible
    from workbench.result_rejection import later_review

    if later_review(c.connection.driver_connection, row['id']):
        raise DeliveryHeld('A later independent review prevents delivery of this patch. Resolve that review before requesting publication.')

    if row["status"] != "checked" or grant["binding"] != digest(grant["snapshot"]):
        raise DeliveryHeld("A bound independently checked real result is required.")
    linked = grant["snapshot"].get("workflow_task")
    if not linked or not task_execution.current(c, linked):
        raise DeliveryHeld("The task or approved scope changed. Verified history is retained.")
    ledger = (
        c.execute(select(model_attempts).where(model_attempts.c.execution_id == row["id"]))
        .mappings()
        .first()
    )
    result = row["result"] or {}
    if (
        not ledger
        or ledger["id"] != row["fence"]
        or ledger["outcome"] != "checked"
        or ledger["verification"] != "passed"
        or result.get("verification") != "passed"
        or ledger["usage"] != result.get("usage")
        or not usage_eligible(grant["snapshot"], result)
    ):
        raise DeliveryHeld(
            "The exact candidate must have complete bound usage and passing independent checks."
        )


def current(c, row, grant):
    _checked(c, row, grant)
    linked = grant["snapshot"]["workflow_task"]
    approval = (
        c.execute(
            select(proposal_decisions).where(proposal_decisions.c.id == linked["approval"]["id"])
        )
        .mappings()
        .one()
    )
    try:
        policy = task_budget.permission(c, approval)
    except ValueError, HTTPException:
        raise DeliveryHeld("The explicit task and delivery budget expired or changed.") from None
    if (
        not policy
        or policy != grant["snapshot"].get("task_budget")
        or policy["budget"]["publication"] != "draft-pr"
    ):
        raise DeliveryHeld("This task has no explicit draft-PR permission.")
    if grant["snapshot"]["contract"] != policy["offer"]["snapshot"]["specification"]["contract"]:
        raise DeliveryHeld("Approved recipe and executed contract differ.")
    return policy


def preview(row, grant):
    linked = grant["snapshot"]["workflow_task"]

    # Publish approved, bounded purpose and check status, never raw model/tool logs,
    # local configuration, credential values or host filesystem locations.
    def text(value, limit):
        value = "".join(c for c in str(value) if c == "\n" or ord(c) >= 32)[:limit]
        return value.replace("<", "&lt;").replace(">", "&gt;").replace("@", "＠")

    title = text(linked["task"]["title"], 150).replace("\n", " ")
    result = row["result"]
    manifest = result["manifest"]
    labels = ["Lua syntax: " + p for p in grant["snapshot"]["contract"]["paths"]] + [
        "Focused task checks",
        "Frozen independent behavior check",
        "Complete applicable repository suite",
    ]
    if len(labels) != len(result["checks"]):
        raise DeliveryHeld("Fixed check inventory differs from the delivery summary.")
    lines = [
        text(linked["task"]["purpose"], 1800),
        "",
        "Approved scope",
        *["- " + text(s, 300) for s in linked["approved_scope"][:8]],
        "",
        "Changes",
        *["- " + text(f["path"], 240) for f in result["files"]],
        "",
        "Independent checks",
        *["- " + text(label, 300) + ": passed (exit 0)" for label in labels],
        "",
        "Limitations and human review",
        "- Offline checks do not establish physical game/client behavior.",
        "- Review the changes before merging. No automatic merge or deployment.",
        "",
        "Provenance",
        *(
            [
                "Authorship: completed by Codex in the explicitly authorized current session; prior failed Qwen contribution retained. No local-model success is claimed."
            ]
            if result.get("authorship", {}).get("kind") == "explicit-current-session"
            else []
        ),
        "Task: " + linked["task"]["id"],
        "Execution: " + row["id"],
        "Base: " + manifest["base"],
        "Candidate: " + result["candidate_id"],
        "<!-- workbench-delivery:" + row["id"] + ":" + result["candidate_id"] + " -->",
    ]
    body = "\n".join(lines)
    if len(body.encode()) > 16384:
        raise DeliveryHeld("PR summary exceeds the bounded publication size.")
    return dict(
        title=title,
        body=body,
        draft=True,
        category=grant["snapshot"]["task_budget"]["offer"]["snapshot"]["specification"].get(
            "category", "Bug fix"
        ),
        task_id=linked["task"]["id"],
    )


def _load(c, identity):
    row = c.execute(select(requests).where(requests.c.id == identity)).mappings().first()
    if not row:
        raise DeliveryHeld("The saved execution could not be found.")
    grant = c.execute(select(grants).where(grants.c.id == row["grant_id"])).mappings().one()
    return dict(row), dict(grant)


def _inputs(engine, identity):
    with engine.connect() as c:
        row, grant = _load(c, identity)
        policy = current(c, row, grant)
    return row, grant, policy


def _snapshot(row, grant, policy, result):
    identity = row["id"]
    return dict(
        version=1,
        execution_id=identity,
        grant_id=grant["id"],
        grant_binding=grant["binding"],
        task_budget=policy,
        candidate_id=result["candidate_id"],
        result_digest=digest(result),
        manifest=result["manifest"],
        evidence_root=grant["snapshot"]["recovery"]["root"],
        preview=preview(row, grant),
        repository=grant["snapshot"]["contract"]["repository"],
        base=grant["snapshot"]["contract"]["base"],
        base_branch=policy["offer"]["snapshot"]["specification"]["base_branch"],
        branch="maintenance/workbench-" + UUID(identity).hex[:20],
    )


def _enqueue(engine, row, grant, snapshot):
    identity = row["id"]
    with write_transaction(engine) as c:
        latest, latest_grant = _load(c, identity)
        if latest_grant != grant or digest(latest["result"]) != snapshot["result_digest"]:
            raise DeliveryHeld("The checked result changed before delivery was saved.")
        if "checked_approval" in snapshot:
            _saved_policy(c, latest, latest_grant)
        else:
            current(c, latest, latest_grant)
        existing = c.execute(select(records).where(records.c.execution_id == identity)).mappings().first()
        binding = digest(snapshot)
        if existing:
            if "checked_approval" in snapshot and existing["binding"] != binding:
                raise DeliveryHeld("This result already has a different delivery record.")
            return False
        c.execute(records.insert().values(
            id=str(uuid5(UUID(identity), "workbench-draft-delivery-v1")),
            execution_id=identity,
            task_id=snapshot["preview"]["task_id"],
            binding=binding,
            snapshot=snapshot,
            status="pending",
            message="Verified changes saved. Waiting for authorized draft-PR delivery.",
            state={},
            created_at=execution.now(),
            finished_at=None,
        ))
    return True


def _saved_policy(c, row, grant):
    """The earlier run supplies evidence only. Its publication hold remains intact."""
    _checked(c, row, grant)
    saved = grant["snapshot"].get("task_budget") or {}
    linked = grant["snapshot"]["workflow_task"]
    approval = c.execute(
        select(proposal_decisions).where(proposal_decisions.c.id == linked["approval"]["id"])
    ).mappings().one()
    historical = task_budget.permission(c, approval, require_current=False)
    if historical != saved or not saved or saved["budget"]["publication"] != "hold":
        raise DeliveryHeld("The original held run and recipe no longer match their saved records.")
    spec = saved["offer"]["snapshot"]["specification"]
    contract = grant["snapshot"]["contract"]
    result = row["result"]
    if (
        spec["contract"] != contract
        or contract["repository"] != "Twich-Team/TwichUI"
        or spec["base_branch"] != "main"
        or spec["draft"] is not True
        or result["manifest"]["base"] != contract["base"]
        or result["manifest"].get("workflow_task_binding") != digest(linked)
        or result.get("inference_ran") is not True
        or not result["files"]
        or any(check["status"] != "pass" or check["rc"] != 0 for check in result["checks"])
    ):
        raise DeliveryHeld("The saved result does not qualify for exact draft delivery.")
    return saved


def _saved_inputs(engine, identity):
    with engine.connect() as c:
        row, grant = _load(c, identity)
        policy = _saved_policy(c, row, grant)
    result = _proof(row, grant)
    return row, grant, result, _snapshot(row, grant, policy, result)


def review_checked(engine, identity):
    """Offline review of the exact destination, candidate and text to be approved."""
    _, _, result, snapshot = _saved_inputs(engine, identity)
    return {
        **{k: snapshot[k] for k in ("execution_id", "repository", "base_branch", "base",
                                      "branch", "candidate_id", "preview")},
        "files": [entry["path"] for entry in result["files"]],
        "binding": digest(snapshot),
    }


def _checked_approval(root, snapshot):
    path = root / "checked-approval.json"
    if (root.resolve() != root or not root.is_dir() or root.stat().st_uid != os.getuid()
            or root.stat().st_mode & 0o077 or path.is_symlink() or not path.is_file()
            or path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077
            or path.stat().st_size > 4096):
        raise DeliveryHeld("An owned private approval for this exact checked draft is required.")
    approval = json.loads(path.read_text())
    if (
        not isinstance(approval, dict)
        or set(approval) != {"execution_id", "binding", "owner_instruction_sha256"}
        or approval["execution_id"] != snapshot["execution_id"]
        or approval["binding"] != digest(snapshot)
        or not isinstance(approval["owner_instruction_sha256"], str)
        or not re.fullmatch("[0-9a-f]{64}", approval["owner_instruction_sha256"])
    ):
        raise DeliveryHeld("The publication approval does not match the exact saved draft.")
    return approval


def authorize_checked(engine, identity, root):
    row, grant, _, snapshot = _saved_inputs(engine, identity)
    approval = _checked_approval(root, snapshot)
    return _enqueue(engine, row, grant, {**snapshot, "checked_approval": approval})


def enqueue_one(engine, execution_id=None):
    with engine.connect() as c:
        if not inspect(c).has_table(records.name):
            return False
        rows = (
            c.execute(
                select(requests.c.id)
                .join(grants, requests.c.grant_id == grants.c.id)
                .outerjoin(records, records.c.execution_id == requests.c.id)
                .where(
                    requests.c.status == "checked",
                    records.c.id.is_(None),
                    *([requests.c.id == execution_id] if execution_id else []),
                    grants.c.snapshot["task_budget"]["budget"]["publication"].as_string()
                    == "draft-pr",
                )
                .order_by(requests.c.finished_at)
                .limit(16)
            )
            .scalars()
            .all()
        )
    for identity in rows:
        try:
            row, grant, policy = _inputs(engine, identity)
            result = _proof(row, grant)
            snapshot = _snapshot(row, grant, policy, result)
            if _enqueue(engine, row, grant, snapshot):
                return True
        except DeliveryHeld, ValueError, HTTPException:
            continue
    return False


def _save(engine, identity, fence, state, status, message):
    with write_transaction(engine) as c:
        row = c.execute(select(records).where(records.c.id == identity)).mappings().one()
        if row["state"].get("fence") != fence or row["status"] not in ACTIVE:
            raise DeliveryHeld("Delivery ownership changed; late result fenced.")
        c.execute(
            records.update()
            .where(records.c.id == identity)
            .values(
                state=state,
                status=status,
                message=message,
                finished_at=execution.now() if status not in ACTIVE else None,
            )
        )


def _activation(snapshot):
    root = os.environ.get("WORKBENCH_PUBLICATION_ROOT")
    if not root:
        return None
    folder = Path(root)
    path = folder / "activation.json"
    if (
        folder.is_symlink()
        or not folder.is_dir()
        or folder.stat().st_uid != os.getuid()
        or path.is_symlink()
        or not path.is_file()
        or path.stat().st_uid != os.getuid()
        or path.stat().st_mode & 0o077
        or path.stat().st_size > 8192
    ):
        raise DeliveryHeld("A private owned one-session publication activation is required.")
    data = json.loads(path.read_text())
    if (
        data.get("decision_id") != snapshot["task_budget"]["decision_id"]
        or data.get("execution_id") != snapshot["execution_id"]
        or data.get("repository") != snapshot["repository"]
        or data.get("base") != snapshot["base"]
        or data.get("max_prs") != 1
        or data.get("draft") is not True
        or datetime.now(timezone.utc) >= datetime.fromisoformat(data["expires_at"])
        or datetime.fromisoformat(data["expires_at"])
        > datetime.fromisoformat(snapshot["task_budget"]["expires_at"])
    ):
        raise DeliveryHeld("The one-session publication activation is expired or mismatched.")
    return folder


_delivery_lock = threading.Lock()


def process_one(engine, publisher=None, stop_event=None, execution_id=None, *, checked_root=None):
    # PID ownership alone does not exclude two threads in the same worker.
    if not _delivery_lock.acquire(blocking=False):
        return False
    try:
        return _process_one(engine, publisher, stop_event, execution_id, checked_root)
    finally:
        _delivery_lock.release()


def _process_one(engine, publisher, stop_event, execution_id, checked_root):
    if checked_root is not None and execution_id is None:
        raise DeliveryHeld("A checked approval must target one exact execution.")
    if checked_root is None and enqueue_one(engine, execution_id):
        return True
    if checked_root is None and publisher is None and os.environ.get("WORKBENCH_PUBLICATION_ROOT"):
        path = Path(os.environ["WORKBENCH_PUBLICATION_ROOT"]) / "activation.json"
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_mode & 0o077
            or path.stat().st_size > 8192
        ):
            return False
        activation = json.loads(path.read_text())
        selected = activation.get("execution_id")
        if not selected or (execution_id and selected != execution_id):
            return False
        execution_id = selected
    with engine.connect() as c:
        if not inspect(c).has_table(records.name):
            return False
        row = (
            c.execute(
                select(records)
                .where(
                    records.c.status.in_(ACTIVE),
                    (records.c.snapshot["checked_approval"].as_string().is_not(None)
                     if checked_root is not None else
                     records.c.snapshot["checked_approval"].as_string().is_(None)),
                    *([records.c.execution_id == execution_id] if execution_id else []),
                )
                .order_by(records.c.created_at)
                .limit(1)
            )
            .mappings()
            .first()
        )
        if not row:
            return False
        row = dict(row)
    if stop_event is not None and stop_event.is_set():
        return False
    state = dict(row["state"])
    owner = execution.owner_identity()
    if state.get("owner") and state["owner"] != owner and execution.alive(state["owner"]):
        return False
    if state.get("retry_after") and datetime.now(timezone.utc) < datetime.fromisoformat(
        state["retry_after"]
    ):
        return False
    try:
        if row["binding"] != digest(row["snapshot"]):
            raise DeliveryHeld("Saved delivery inputs failed integrity checks.")
        if checked_root is not None:
            _, _, result, saved = _saved_inputs(engine, row["execution_id"])
            approval = _checked_approval(checked_root, saved)
            if {**saved, "checked_approval": approval} != row["snapshot"]:
                raise DeliveryHeld("The approved delivery and frozen result no longer match.")
            root = checked_root
        else:
            original, grant, policy = _inputs(engine, row["execution_id"])
            result = _proof(original, grant)
            root = None
        if digest(result) != row["snapshot"]["result_digest"] or (
            checked_root is None and policy != row["snapshot"]["task_budget"]
        ):
            raise DeliveryHeld(
                "Candidate, checks or owner budget changed; earlier evidence is retained."
            )
        if publisher is None:
            root = root or _activation(row["snapshot"])
            if root is None:
                return False
            from workbench.publication_adapter import PilotGitHub

            publisher = PilotGitHub(
                root, row["snapshot"], scratch_root=root if checked_root is not None else None
            )
    except (ValueError, HTTPException, OSError) as error:
        with write_transaction(engine) as c:
            c.execute(
                records.update()
                .where(records.c.id == row["id"], records.c.status == row["status"],
                       records.c.state == row["state"])
                .values(status="held", message=_hold_message(error), finished_at=execution.now())
            )
        return True
    with write_transaction(engine) as c:
        latest = c.execute(select(records).where(records.c.id == row["id"])).mappings().one()
        if latest["state"] != row["state"] or latest["status"] not in ACTIVE:
            return False
        state.update(
            owner=owner, fence=str(uuid4()), started_at=state.get("started_at", execution.now())
        )
        c.execute(records.update().where(records.c.id == row["id"]).values(state=state))
    fence = state["fence"]
    snapshot = row["snapshot"]

    def permitted():
        if stop_event is not None and stop_event.is_set():
            return False
        try:
            with engine.connect() as c:
                fresh = c.execute(select(records).where(records.c.id == row["id"])).mappings().one()
                if fresh["state"].get("fence") != fence or fresh["status"] not in ACTIVE:
                    return False
                original, grant = _load(c, row["execution_id"])
                if checked_root is not None:
                    _saved_policy(c, original, grant)
                    base_snapshot = {k: v for k, v in snapshot.items() if k != "checked_approval"}
                    return (
                        grant["binding"] == snapshot["grant_binding"]
                        and digest(original["result"]) == snapshot["result_digest"]
                        and _checked_approval(checked_root, base_snapshot) == snapshot["checked_approval"]
                    )
                return (
                    current(c, original, grant) == snapshot["task_budget"]
                    and datetime.now(timezone.utc)
                    < datetime.fromisoformat(state["started_at"])
                    + timedelta(seconds=policy["budget"]["publication_seconds"])
                    and (not getattr(publisher, "real", False) or _activation(snapshot) is not None)
                )
        except (ValueError, HTTPException, OSError):
            return False

    publisher.permitted = permitted
    try:
        if not permitted():
            raise DeliveryHeld("Delivery budget, stop state or activation is no longer current.")
        if not state.get("pr_started") and publisher.base_head(snapshot) != snapshot["base"]:
            raise DeliveryHeld(
                "The target base moved. Renewed independent verification is required; no automatic rebase."
            )
        if not state.get("commit"):
            commit = publisher.prepare(snapshot, result)
            if not re.fullmatch("[0-9a-f]{40}", commit):
                raise DeliveryHeld("Invalid prepared commit identity.")
            state["commit"] = commit
            _save(
                engine,
                row["id"],
                fence,
                state,
                "preparing",
                "Verified candidate prepared for draft delivery.",
            )
            return True
        if not state.get("pushed"):
            remote = publisher.branch_head(snapshot)
            if remote and remote != state["commit"]:
                raise DeliveryHeld("The delivery branch has unrelated changes; no overwrite.")
            if remote == state["commit"]:
                state["pushed"] = True
                state.pop("uncertain", None)
                _save(
                    engine,
                    row["id"],
                    fence,
                    state,
                    "creating",
                    "Branch confirmed. Preparing its draft PR.",
                )
                return True
            if state.get("push_started"):
                raise UncertainWrite("push")
            state["push_started"] = True
            _save(engine, row["id"], fence, state, "pushing", "Sending this verified branch once.")
            publisher.push(snapshot, state["commit"])
            state["uncertain"] = "push"
            _save(
                engine,
                row["id"],
                fence,
                state,
                "reconciling",
                "Checking the recorded branch before continuing.",
            )
            return True
        existing = publisher.find_pr(snapshot, state["commit"])
        if existing:
            if not publisher.valid_pr(snapshot, state["commit"], existing):
                raise DeliveryHeld(
                    "An existing PR differs from this exact open draft; no replacement or update."
                )
            state.update(
                published=True,
                source="github" if publisher.real else "controlled",
                url=existing.get("url") if publisher.real else None,
                logical_reference=existing.get("reference"),
            )
            _save(
                engine,
                row["id"],
                fence,
                state,
                "published",
                "Draft PR created and confirmed. Human review and merge remain."
                if publisher.real
                else "Controlled draft delivery confirmed. No real PR was created.",
            )
            return True
        if state.get("pr_started"):
            raise UncertainWrite("pr")
        if publisher.branch_head(snapshot) != state["commit"]:
            raise DeliveryHeld("The delivery branch changed before PR creation.")
        state["pr_started"] = True
        _save(
            engine,
            row["id"],
            fence,
            state,
            "creating",
            "Creating one draft PR for the verified branch.",
        )
        publisher.create_pr(snapshot, state["commit"])
        state["uncertain"] = "pr"
        _save(
            engine,
            row["id"],
            fence,
            state,
            "reconciling",
            "Checking the draft PR before recording delivery.",
        )
    except UncertainWrite:
        state["reconciliation_reads"] = state.get("reconciliation_reads", 0) + 1
        held = state["reconciliation_reads"] >= 3
        state["retry_after"] = (datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat()
        _save(
            engine,
            row["id"],
            fence,
            state,
            "held" if held else "reconciling",
            "Publication outcome remains uncertain. Evidence retained; no write will be repeated."
            if held
            else "Waiting to reconcile the earlier write; implementation will not run again.",
        )
    except DeliveryHeld as error:
        _save(engine, row["id"], fence, state, "held", str(error)[:600])
    except OSError, RuntimeError:
        # A write intent is already durable. Never send it again after an unknown
        # response, including a receipt-store failure or process interruption.
        state["uncertain"] = (
            "pr"
            if state.get("pr_started")
            else "push"
            if state.get("push_started")
            else "preparation"
        )
        state["reconciliation_reads"] = state.get("reconciliation_reads", 0) + 1
        held = state["reconciliation_reads"] >= 3
        state["retry_after"] = (datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat()
        _save(
            engine,
            row["id"],
            fence,
            state,
            "held" if held else "reconciling",
            "Delivery needs attention; verified changes retained. No implementation retry."
            if held
            else "Temporary delivery failure. Waiting to reconcile saved work.",
        )
    return True


def _hold_message(error):
    if isinstance(error, OSError):
        return "Required local delivery evidence is unavailable."
    if isinstance(error, HTTPException):
        return "The saved task or its approval is no longer current."
    return str(error)[:600]


def deliver_checked(engine, identity, root, stopped):
    import shutil
    from workbench.publication_adapter import PilotGitHub

    # Preserve the production disk reserve and bound this one staging copy.
    if shutil.disk_usage(root).free < 5 << 30:
        raise DeliveryHeld("Insufficient free space for isolated draft preparation.")
    authorize_checked(engine, identity, root)
    while not stopped.is_set():
        worked = process_one(engine, stop_event=stopped, execution_id=identity, checked_root=root)
        delivery = view(engine, identity)
        if delivery["status"] not in ACTIVE:
            break
        if not worked:
            stopped.wait(0.5)
    delivery = view(engine, identity)
    if delivery["published"]:
        with engine.connect() as c:
            row = c.execute(select(records).where(records.c.execution_id == identity)).mappings().one()
        try:
            PilotGitHub(root, row["snapshot"], scratch_root=root).cleanup(row["state"]["commit"])
        except (OSError, ValueError, RuntimeError):
            # A completed PR must remain visible even if its local scratch needs attention.
            delivery["message"] += " The isolated delivery copy was retained for inspection."
    return delivery


def view(engine, identity):
    with engine.connect() as c:
        if not inspect(c).has_table(records.name):
            return None
        row = (
            c.execute(select(records).where(records.c.execution_id == identity)).mappings().first()
        )
        if not row:
            return None
        row = dict(row)
    eligible = True
    current_message = "Current explicit task budget and scope remain valid."
    try:
        if "checked_approval" in row["snapshot"]:
            with engine.connect() as c:
                original, grant = _load(c, identity)
                _saved_policy(c, original, grant)
            current_message = "Current checked result and separately recorded draft approval."
        else:
            _inputs(engine, identity)
    except (ValueError, HTTPException, OSError) as error:
        eligible = False
        current_message = str(error)[:600]
    url = row["state"].get("url")
    real = (
        row["state"].get("source") == "github"
        and isinstance(url, str)
        and re.fullmatch(r"https://github.com/Twich-Team/TwichUI/pull/[1-9][0-9]{0,9}", url)
        is not None
    )
    return dict(
        id=row["id"],
        status=row["status"],
        message=row["message"],
        preview=row["snapshot"]["preview"],
        repository=row["snapshot"]["repository"],
        base_branch=row["snapshot"]["base_branch"],
        candidate_id=row["snapshot"]["candidate_id"],
        source=row["state"].get("source"),
        url=url if real else None,
        published=row["status"] == "published" and real,
        current_eligible=eligible,
        current_message=current_message,
    )


def resume_preparation(engine, execution_id, command_id, owner_instruction_sha256, publisher):
    """Trusted one-off repair of diagnosed LOCAL staging failure, never a model retry.

    Preserve the prior held outcome and original delivery clock. Refuse after any
    remote intent; uncertain writes use the existing reconciliation path instead.
    """
    UUID(command_id)
    if not re.fullmatch("[0-9a-f]{64}", owner_instruction_sha256):
        raise DeliveryHeld("Exact owner authorization provenance required.")
    original, grant, policy = _inputs(engine, execution_id)
    result = _proof(original, grant)
    with engine.connect() as c:
        row = dict(
            c.execute(select(records).where(records.c.execution_id == execution_id))
            .mappings()
            .one()
        )
    state = dict(row["state"])
    prior = state.get("preparation_recovery")
    if prior:
        if (
            prior["command_id"] != command_id
            or prior["owner_instruction_sha256"] != owner_instruction_sha256
        ):
            raise DeliveryHeld("Conflicting preparation recovery; original recovery retained.")
        return row["status"]
    if (
        row["status"] != "held"
        or row["message"] != "Prepared patch changed unrelated paths."
        or any(state.get(k) for k in ("commit", "push_started", "pr_started", "uncertain"))
        or digest(row["snapshot"]) != row["binding"]
        or digest(result) != row["snapshot"]["result_digest"]
        or datetime.now(timezone.utc)
        >= datetime.fromisoformat(state["started_at"])
        + timedelta(seconds=policy["budget"]["publication_seconds"])
    ):
        raise DeliveryHeld(
            "Only this bound local preparation failure can be recovered within its original clock."
        )
    if getattr(publisher, "real", False) and _activation(row["snapshot"]) is None:
        raise DeliveryHeld("Current exact publication activation required.")
    publisher.permitted = lambda: _inputs(engine, execution_id)[2] == policy
    if not publisher.prepared_patch_matches(row["snapshot"], result):
        raise DeliveryHeld(
            "Owned checkout is not the exact already-applied frozen patch; no reset."
        )
    from workbench.real_workspace import sha

    proof = dict(
        command_id=command_id,
        owner_instruction_sha256=owner_instruction_sha256,
        at=execution.now(),
        adapter_sha256=sha(Path(__file__).with_name("publication_adapter.py").read_bytes()),
        previous_outcome=dict(
            status=row["status"],
            message=row["message"],
            finished_at=row["finished_at"],
            state=state,
        ),
    )
    with write_transaction(engine) as c:
        current(c, original, grant)
        actual = c.execute(select(records).where(records.c.id == row["id"])).mappings().one()
        if actual["status"] != row["status"] or actual["state"] != row["state"]:
            raise DeliveryHeld("Delivery changed during local recovery.")
        c.execute(
            records.update()
            .where(records.c.id == row["id"])
            .values(
                status="pending",
                finished_at=None,
                message="Resuming the exact saved candidate after a verified local staging correction; earlier failure retained.",
                state={**state, "preparation_recovery": proof},
            )
        )
    return "pending"
