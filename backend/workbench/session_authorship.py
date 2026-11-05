"""Explicit current-Codex-session input. No inference/provider, never automatic fallback.

The owner supplies this exceptional author choice through trusted preparation.
Existing worker/grant/freezer/checks/delivery remain canonical. Unavailable Codex
usage is preserved, never represented as measured local inference or zero tokens.
"""

import json
import os
from pathlib import Path
from workbench.real_workspace import Runtime, sha, durable
from workbench import real_execution as job, real_confine as confine

MODE = "explicit-current-session"


def contract_allowed(contract):
    return (
        contract.get("provider") == "current-session"
        and contract.get("model") == "codex-current-session"
        and contract.get("authoring") == MODE
        and contract.get("fallback_allowed") is False
    )


def usage_eligible(snapshot, result):
    if not contract_allowed(snapshot["contract"]):
        return result.get("usage", {}).get("status") == "complete"
    return authorship_valid(snapshot["contract"], result, snapshot["runtime"].get("authorship"))


def authorship_valid(contract, result, expected=None):
    if not contract_allowed(contract):
        return False
    proof = result.get("authorship")
    if not isinstance(proof, dict) or (expected is not None and proof != expected):
        return False
    try:
        return (
            proof["kind"] == MODE
            and proof["author"] == "Codex current session"
            and len(proof["owner_authorization_sha256"]) == 64
            and proof["files"]
            and set(proof["files"]).issubset(contract["paths"])
            and all(result["manifest"]["tree"][p] == h for p, h in proof["files"].items())
            and result["manifest"].get("authorship") == proof
            and result.get("inference_ran") is False
            and result.get("usage", {}).get("status") == "unavailable"
            and result["usage"].get("values") is None
            and result["usage"].get("provenance", {}).get("method")
            == "current Codex session; no separately measurable token receipt"
        )
    except KeyError, TypeError:
        return False


class SessionAuthoredRuntime(Runtime):
    def author_input(self):
        path = self.root / "session-authored.json"
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_uid != os.getuid()
            or path.stat().st_mode & 0o077
            or path.stat().st_size > 16384
        ):
            raise ValueError("Private explicit session-authorship input required")
        value = json.loads(path.read_text())
        if (
            not contract_allowed(self.execution_contract)
            or value["kind"] != MODE
            or value["base"] != self.base
        ):
            raise ValueError("Explicit session author/base differs from the registered contract")
        from workbench.owner_task import instruction

        instruction(
            value["owner_instruction"],
            value["owner_authorization_sha256"],
            "supported-task-to-draft-pr-v1",
        )
        files = value["files"]
        if set(files) != set(self.paths):
            raise ValueError("Exact registered authored file set required")
        for name, h in files.items():
            p = self.root / "session-files" / name
            if (
                any(
                    x.is_symlink()
                    for x in [p, *p.parents]
                    if x == self.root or self.root in x.parents
                )
                or not p.is_file()
                or p.stat().st_uid != os.getuid()
                or not p.resolve().is_relative_to((self.root / "session-files").resolve())
                or p.stat().st_size > 65536
                or sha(p.read_bytes()) != h
            ):
                raise ValueError("Session-authored file identity failed")
        return value

    def context_sha(self):
        return sha((self.root / "session-authored.json").read_bytes())

    def identity(self):
        value = super().identity()
        proof = self.author_input()
        value.update(
            model="codex-current-session",
            model_path=None,
            authorship=proof,
            authoring=MODE,
            new_provider_invocation=False,
        )
        value["helper_sha256"]["session_authorship.py"] = sha(Path(__file__).read_bytes())
        return value

    def preflight(self):
        gates = self.static_preflight()
        proof = self.author_input()
        self.enforce_storage()
        cg = Path(confine.root())
        bounded = all(
            (cg / n).read_text().strip() == v
            for n, v in {
                "memory.max": str(6 << 30),
                "memory.swap.max": "0",
                "pids.max": "128",
                "cpu.max": "200000 100000",
            }.items()
        )
        gates += [
            dict(
                name="trusted verification confinement", status="passed" if bounded else "blocked"
            ),
            dict(
                name="explicit current-session author; no model service used",
                status="passed" if proof["author"] == "Codex current session" else "blocked",
            ),
        ]
        self.gates = gates
        return gates

    def execute(self, identity, fence, permitted):
        if not permitted() or any(g["status"] != "passed" for g in self.preflight()):
            raise ValueError("Session-authored task gates failed")
        self.authoring_provenance = self.author_input()
        self._request_id, self._fence, self._context_sha = identity, fence, self.context_sha()
        durable(
            self.root / ("ownership-" + identity + ".json"),
            json.dumps(dict(owner=job.owner_identity(), cgroup=confine.root())).encode(),
        )
        for path in self.paths:
            if not permitted():
                raise ValueError("Grant changed before applying session-authored files")
            (self.workspace / path).write_bytes((self.root / "session-files" / path).read_bytes())
        self.scope()
        if hasattr(self, "phase"):
            self.phase()
        feedback = self.candidate_feedback(permitted)
        if feedback["status"] != "pass":
            raise ValueError(
                "Session-authored candidate failed independent checks: "
                + json.dumps(feedback, ensure_ascii=True)
            )
        return self.freeze_candidate(
            identity,
            fence,
            permitted,
            dict(correction_count=0, read_input_errors=0, tool_input_errors=0),
            dict(usage=self.failure_evidence(identity, fence)["usage"]),
        )

    def failure_evidence(self, identity, fence):
        return dict(
            inference_ran=False,
            authorship=self.author_input(),
            usage=dict(
                status="unavailable",
                values=None,
                known_values=None,
                provenance=dict(
                    method="current Codex session; no separately measurable token receipt",
                    local_model_requests=0,
                    request_id=identity,
                    fence=fence,
                ),
            ),
        )
