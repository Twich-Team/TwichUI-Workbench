"""One coherent combined feature artifact through the existing delivery owner.

Local commit preparation is credential-free. This module never authorizes an
external push or PR; the existing separately recorded draft policy owns that.
No task budget, model ledger, generated owner decision or fake PR is created.
"""

from pathlib import Path
import json
from uuid import UUID

from sqlalchemy import select

from workbench import feature_verification, feature_steps, intake
from workbench.db import (
    planning_jobs,
    task_sessions,
    real_publications,
    real_execution_requests,
    real_execution_grants,
    write_transaction,
)
from workbench.real_workspace import durable
from workbench.visual_scope import digest
from workbench.real_publication import DeliveryHeld

KIND = "combined-feature-artifact-v1"


def policy(c, row, grant, *, already_checked=False, allow_preparing=False):
    if not already_checked:
        feature_verification.checked(c, row, grant)
    spec = grant["snapshot"]["feature_verification"]
    approval, _ = feature_steps.parent(c, spec["decision_id"], active=allow_preparing)
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
    parent = intake.require(c, task_sessions, approval["id"])
    scope = job["payload"].get("current_step_scope") or {}
    checked = job["payload"].get("completed_checks", [])
    if len(checked) != 1 or checked[0] != dict(
        task_id=spec["verification_task_id"],
        execution_id=row["id"],
        candidate_id=row["result"]["candidate_id"],
    ):
        raise DeliveryHeld(
            "The one complete feature verification must match its actual checked result."
        )
    done = {r["task_id"] for r in job["payload"]["completed_steps"]} | {
        spec["verification_task_id"]
    }
    _, permission = feature_steps.parent(c, approval["id"], active=False)
    remaining = {r["task_id"] for r in permission["scope"]["graph"]} - done
    if (
        parent["state"].get("cancel_requested")
        or job["payload"].get("phase")
        not in (
            {"delivery_ready", "prepare_delivery"}
            if allow_preparing
            else {"delivery_ready", "delivery_published"}
        )
        or scope.get("scope", {}).get("role") != "delivery"
        or remaining != {scope.get("task_id")}
        or job["payload"].get("final_verification_execution") != row["id"]
    ):
        raise DeliveryHeld(
            "Every implementation and verification step must be complete before the one final feature delivery."
        )
    feature_steps.validate_scope(c, scope, active=False)
    other = c.execute(
        select(real_publications.c.id)
        .join(
            real_execution_requests,
            real_publications.c.execution_id == real_execution_requests.c.id,
        )
        .join(
            real_execution_grants, real_execution_requests.c.grant_id == real_execution_grants.c.id
        )
        .where(
            real_execution_grants.c.snapshot["operation_kind"].as_string()
            == feature_verification.KIND,
            real_execution_grants.c.snapshot["feature_verification"]["decision_id"].as_string()
            == approval["id"],
            real_publications.c.execution_id != row["id"],
        )
    ).first()
    if other:
        raise DeliveryHeld(
            "This feature already owns another coherent delivery record. No competing draft will be created."
        )
    value = dict(
        kind=KIND,
        decision_id=approval["id"],
        parent_binding=approval["binding"],
        delivery_task_id=scope["task_id"],
        execution_id=row["id"],
        candidate_id=row["result"]["candidate_id"],
        result_binding=digest(row["result"]),
        specification=dict(
            contract=grant["snapshot"]["contract"],
            identity=spec["identity"],
            base_branch="main",
            draft=True,
            category="Feature",
        ),
        contribution_binding=digest(spec["contributions"]),
        publication="separate-existing-owner-only",
    )
    artifact = {**value, "binding": digest(value)}
    if not allow_preparing:
        from workbench.repair_continuation import private_json

        location = (
            Path(grant["snapshot"]["recovery"]["root"]) / "feature-draft-bundle/feature-draft.json"
        )
        receipt = job["payload"].get("feature_delivery") or {}
        saved = private_json(location, 65536)
        if (
            receipt.get("bundle") != str(location)
            or receipt.get("execution_id") != row["id"]
            or receipt.get("task_id") != scope["task_id"]
            or receipt.get("bundle_sha256") != digest(saved)
            or saved["artifact_binding"] != artifact["binding"]
            or saved["candidate_id"] != row["result"]["candidate_id"]
        ):
            raise DeliveryHeld("The exact coherent draft bundle is unavailable or changed.")
    return artifact


def preview(row, grant):
    linked = grant["snapshot"]["workflow_task"]
    result = row["result"]

    def text(value, limit):
        value = "".join(c for c in str(value) if c == "\n" or ord(c) >= 32)[:limit]
        return value.replace("<", "&lt;").replace(">", "&gt;").replace("@", "＠")

    title = text(linked["original"]["title"], 150).replace("\n", " ")
    spec = grant["snapshot"]["feature_verification"]
    labels = (
        ["Lua syntax: " + p for p in spec["paths"]]
        + ["Focused native checks: " + p for p in spec["independent_checks"]["focused_tests"]]
        + [
            "Frozen independent feature observations",
            "Complete unchanged repository test inventory",
        ]
    )
    if len(labels) != len(result["checks"]):
        raise DeliveryHeld("The combined check inventory differs from the draft summary.")
    lines = [
        text(linked["proposal_review"]["content"]["summary"], 1800),
        "",
        "Approved feature scope",
        *["- " + text(s, 500) for s in linked["proposal_review"]["content"]["scope"]],
        "",
        "Combined changes",
        *["- " + text(f["path"], 240) for f in result["files"]],
        "",
        "Independent verification",
        *["- " + text(label, 300) + ": passed (exit 0)" for label in labels],
        "- Original production exhibited the missing behavior; original defaults and control observations were preserved.",
        "",
        "Implementation contributions",
        *[
            "- " + text(part["task_id"], 240) + " — " + text(part["execution_id"], 80)
            for part in result["contributions"]
        ],
        "- The combined verification operation made no model request. Child model usage remains separately recorded.",
        "",
        "Limitations and review",
        "- Offline checks do not establish behavior or visual parity in the game client.",
        "- Review the complete combined changes before merging. No merge, release or game installation is authorized here.",
        "",
        "Provenance",
        "Parent feature: " + linked['intake_id'],
        "Original base: " + spec["original_base"],
        "Combined checked input: " + spec["feature_input"]["commit"],
        "Candidate: " + result["candidate_id"],
        "<!-- workbench-delivery:" + row["id"] + ":" + result["candidate_id"] + " -->",
    ]
    body = "\n".join(lines)
    composition = spec['independent_checks'].get('evidence_composition')
    if composition:
        body += '\n\nRequirement evidence\n' + '\n'.join(
            '- '+text(r['text'], 500)+': established by '+', '.join(r['evidence'])
            for r in composition['requirements'])
        body += ('\nReused native assertions retain implementation authorship. '
            'Supplement authorship and any later diagnostic corrections are retained separately in the frozen contract.')
    if len(body.encode()) > 16384:
        raise DeliveryHeld("The complete feature summary exceeds the publication text bound.")
    return dict(
        title=title, body=body, draft=True, category="Feature", task_id=linked["task"]["id"]
    )


def snapshot(row, grant, artifact, result):
    return dict(
        version=1,
        execution_id=row["id"],
        grant_id=grant["id"],
        grant_binding=grant["binding"],
        feature_artifact=artifact,
        candidate_id=result["candidate_id"],
        result_digest=digest(result),
        manifest=result["manifest"],
        evidence_root=grant["snapshot"]["recovery"]["root"],
        preview=preview(row, grant),
        repository="Twich-Team/TwichUI",
        base=grant["snapshot"]["contract"]["base"],
        base_branch="main",
        branch="maintenance/workbench-" + UUID(row["id"]).hex[:20],
    )


def publication_specification(snapshot):
    artifact = snapshot["feature_artifact"]
    if (
        artifact["kind"] != KIND
        or artifact["binding"] != digest({k: v for k, v in artifact.items() if k != "binding"})
        or artifact["execution_id"] != snapshot["execution_id"]
        or artifact["candidate_id"] != snapshot["candidate_id"]
        or artifact["result_binding"] != snapshot["result_digest"]
        or artifact["specification"]["contract"]["operation_kind"] != feature_verification.KIND
        or artifact["specification"]["contract"]["base"] != snapshot["base"]
    ):
        raise DeliveryHeld("The combined feature publication artifact changed.")
    return artifact["specification"]


def authorship_valid(contract, result):
    return bool(
        contract.get("operation_kind") == feature_verification.KIND
        and contract.get("inference_allowed") is False
        and contract.get("provider") == "none"
        and result.get("operation_kind") == feature_verification.KIND
        and result.get("inference_ran") is False
        and result.get("usage") is None
        and result.get("contributions")
        and result.get("authorship")
        == dict(kind="checked-feature-steps", contributions_binding=digest(result["contributions"]))
    )


def prepare(engine, job):
    from workbench.feature_source import LocalCheckedCommit, locked
    from workbench.real_publication import _load

    identity = job["payload"]["final_verification_execution"]

    def inputs(*, native=True):
        with engine.connect() as c:
            row, grant = _load(c, identity)
            if native:
                feature_verification.checked(c, row, grant)
            artifact = policy(c, row, grant, already_checked=True, allow_preparing=True)
        return row, grant, row["result"], snapshot(row, grant, artifact, row["result"])

    row, grant, result, saved = inputs()
    artifact = saved["feature_artifact"]
    root = Path(saved["evidence_root"]) / "feature-draft-bundle"
    with locked(root):

        def permitted():
            try:
                return inputs(native=False)[3] == saved
            except ValueError, OSError:
                return False

        adapter = LocalCheckedCommit(root, saved, scratch_root=root)
        adapter.permitted = permitted
        commit = adapter.prepare(saved, result)
        if not permitted() or inputs()[3] != saved:
            raise DeliveryHeld(
                "The complete feature changed while preparing its coherent draft bundle."
            )
        value = dict(
            kind=KIND,
            execution_id=identity,
            candidate_id=result["candidate_id"],
            artifact_binding=artifact["binding"],
            snapshot_binding=digest(saved),
            patch_sha256=result["manifest"]["patch_sha256"],
            local_commit=commit,
            preview=saved["preview"],
            publication="not_requested_by_local_preparation",
        )
        durable(root / "feature-draft.json", json.dumps(value, sort_keys=True).encode())
        (root / "feature-draft.json").chmod(0o600)
    with write_transaction(engine) as c:
        current = intake.require(c, planning_jobs, job["id"])
        parent = intake.require(c, task_sessions, job["payload"]["feature_decision_id"])
        if (
            current["status"] != "queued"
            or current["payload"] != job["payload"]
            or parent["state"].get("cancel_requested")
        ):
            return False
        c.execute(
            planning_jobs.update()
            .where(planning_jobs.c.id == job["id"])
            .values(
                status="completed",
                finished_at=intake.now(),
                payload={
                    **current["payload"],
                    "phase": "delivery_ready",
                    "feature_delivery": dict(
                        task_id=artifact["delivery_task_id"],
                        execution_id=identity,
                        bundle_sha256=digest(value),
                        bundle=str(root / "feature-draft.json"),
                    ),
                },
                message="One coherent draft bundle is ready. Publishing follows the separate saved draft permission.",
            )
        )
        c.execute(
            task_sessions.update()
            .where(task_sessions.c.id == parent["id"])
            .values(
                status="held",
                finished_at=intake.now(),
                message="The complete feature and its coherent draft bundle are ready for review. Publication follows saved permissions.",
            )
        )
    return True


def start(engine, job):
    with write_transaction(engine) as c:
        current = intake.require(c, planning_jobs, job["id"])
        if current["status"] != "queued" or current["payload"] != job["payload"]:
            return False
        scope = current["payload"]["current_step_scope"]
        feature_steps.validate_scope(c, scope)
        if scope["scope"]["role"] != "delivery" or current["payload"]["phase"] != "scope_ready":
            raise DeliveryHeld("A current final delivery step is required.")
        c.execute(
            planning_jobs.update()
            .where(planning_jobs.c.id == job["id"])
            .values(
                payload={**current["payload"], "phase": "prepare_delivery"},
                message="Preparing one coherent draft bundle from the complete verified feature.",
            )
        )
    return True


def prepare_scope(engine, job_id):
    """Fixed local delivery preparation after an audited composed verification.

    The last graph node packages an already checked result; it needs no newly
    generated source/checking program. Keep the existing scope compiler and
    delivery owner, and never issue a provider operation for this local action.
    """
    from workbench import task_execution
    with write_transaction(engine) as c:
        job = intake.require(c, planning_jobs, job_id)
        payload = job['payload']
        approval, permission = feature_steps.parent(c, payload['feature_decision_id'])
        if (job['status'] != 'queued' or payload['phase'] != 'prepare_scopes'
                or not payload.get('evidence_composition') or len(payload.get('completed_checks', [])) != 1):
            return False
        records = payload['completed_steps']
        actual = records[-1]['input']
        done = {r['task_id'] for r in records} | feature_verification.satisfied_tasks(c, approval['id'], actual)
        remaining = [r for r in permission['scope']['graph'] if r['task_id'] not in done]
        if len(remaining) != 1 or not set(remaining[0]['dependencies']) <= done:
            raise DeliveryHeld('Only the final local packaging step can use fixed delivery preparation.')
        task_id = remaining[0]['task_id']
        linked = task_execution.snapshot(c, approval['intake_id'], task_id, approval['id'])
        original = payload['evidence_composition']['scope']['scope']
        scope = feature_steps.compile_scope(c, approval['id'], task_id, dict(
            role='delivery',read_paths=original['read_paths'],writable_paths=[],
            focused_test=original['focused_test'],checks=[dict(acceptance_index=i,
                observation='Package the exact independently checked combined feature: '+criterion[:480],
                control='Require the same frozen candidate, complete result and held publication; never edit source or dispatch a model.')
                for i,criterion in enumerate(linked['task']['acceptance'])],limitation=''),feature_input=actual)
        c.execute(planning_jobs.update().where(planning_jobs.c.id==job_id).values(
            payload={**payload,'phase':'scope_ready','current_step_scope':scope},
            message='Preparing the final local review package from the checked result. No model request is needed.'))
        return True


def view_in(c, job):
    """Show only the actual local artifact and verified publication receipts."""
    import re

    saved = job["payload"].get("feature_delivery")
    if not saved:
        return None
    row = intake.require(c, real_execution_requests, saved["execution_id"])
    grant = intake.require(c, real_execution_grants, row["grant_id"])
    artifact = policy(c, row, grant)
    publication = (
        c.execute(select(real_publications).where(real_publications.c.execution_id == row["id"]))
        .mappings()
        .first()
    )
    url = publication["state"].get("url") if publication else None
    published = bool(
        publication
        and publication["status"] == "published"
        and publication["binding"] == digest(publication["snapshot"])
        and publication["snapshot"].get("feature_artifact") == artifact
        and publication["state"].get("source") == "github"
        and isinstance(url, str)
        and re.fullmatch(r"https://github.com/Twich-Team/TwichUI/pull/[1-9][0-9]{0,9}", url)
    )
    return dict(
        execution_id=row["id"],
        delivery_task_id=saved["task_id"],
        published=published,
        draft_url=url if published else None,
        verified_requirements=[r['text'] for r in grant['snapshot']['feature_verification']
            ['independent_checks'].get('evidence_composition', {}).get('requirements', [])
            if r['kind'] == 'behavior'],
        message="The feature draft PR is ready for review."
        if published
        else "One coherent feature draft bundle is ready for review. Publishing follows your saved permissions.",
    )


def reconcile(engine):
    with engine.connect() as c:
        jobs = list(
            c.execute(
                select(planning_jobs).where(
                    planning_jobs.c.kind == "feature_execution",
                    planning_jobs.c.payload["phase"].as_string() == "delivery_ready",
                    planning_jobs.c.payload["feature_delivery"].as_string().is_not(None),
                )
            ).mappings()
        )
    for job in jobs:
        try:
            with engine.connect() as c:
                result = view_in(c, job)
            if not result or not result["published"]:
                continue
            with write_transaction(engine) as c:
                current = intake.require(c, planning_jobs, job["id"])
                parent = intake.require(c, task_sessions, job["payload"]["feature_decision_id"])
                if current["payload"] != job["payload"]:
                    continue
                c.execute(
                    planning_jobs.update()
                    .where(planning_jobs.c.id == job["id"])
                    .values(
                        status="completed",
                        finished_at=intake.now(),
                        payload={**current["payload"], "phase": "delivery_published"},
                        message="One complete feature draft PR is ready for review. No merge or game installation was performed.",
                    )
                )
                c.execute(
                    task_sessions.update()
                    .where(task_sessions.c.id == parent["id"])
                    .values(
                        status="completed",
                        finished_at=intake.now(),
                        message="The feature draft PR is ready for review.",
                    )
                )
            return True
        except ValueError, OSError:
            continue
    return False
