"""Workbench adapter for the inspected pilot Git/GitHub publication mechanism.

One canonical Workbench delivery row owns state. No pilot queue, policy, approval,
bookkeeping, workspace or publishing entry point is called or modified. Uses the
pilot's existing fixed command/credential helpers and the same isolated branch ->
commit -> non-force push -> draft lookup/create/verify sequence, extended to exact
modified files and crash-safe commit reuse. No arbitrary shell interface.
"""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
from subprocess import TimeoutExpired
import tempfile
import shutil

from workbench.real_publication import DeliveryHeld, UncertainWrite

PILOT = Path("/home/rich/twichui-pilot/batch/publish.py")
PILOT_SHA = "e8940795227e137530f1d13a51c1e790bf616f8942bd043f018a63358ab9ed5d"


class PilotGitHub:
    real = True

    def __init__(self, root, snapshot, *, scratch_root=None):
        self.root = Path(root).resolve()
        self.snapshot = snapshot
        self.scratch_root = Path(scratch_root or tempfile.gettempdir()).resolve()
        self.permitted = lambda: False
        self._module = None
        self.workspace = self.root / ("delivery-" + snapshot["execution_id"])
        self.location = self.root / "publication-workspace.json"
        if self.location.exists():
            self.workspace = self._owned_checkout(allow_absent=True)

    def _owned_checkout(self, create=False, allow_absent=False):
        from workbench.real_workspace import durable

        prefix = "workbench-publish-" + self.snapshot["execution_id"][:8] + "-"
        if not self.location.exists():
            if not create:
                raise DeliveryHeld("Publication scratch identity is unavailable.")
            if not self.permitted():
                raise DeliveryHeld(
                    "Current publication permission required before scratch allocation."
                )
            legacy = self.root / ("delivery-" + self.snapshot["execution_id"])
            if legacy.exists():
                raise DeliveryHeld("An earlier delivery checkout needs inspection; no overwrite.")
            parent = Path(tempfile.mkdtemp(prefix=prefix, dir=self.scratch_root))
            stat = parent.stat()
            record = dict(
                execution_id=self.snapshot["execution_id"],
                path=str(parent / "checkout"),
                parent_identity=[stat.st_dev, stat.st_ino],
                owner=os.getuid(),
            )
            try:
                durable(self.location, json.dumps(record, sort_keys=True).encode())
            except BaseException:
                parent.rmdir()
                raise
        if (
            self.location.is_symlink()
            or self.location.stat().st_uid != os.getuid()
            or self.location.stat().st_size > 4096
        ):
            raise DeliveryHeld("Owned bounded scratch identity required.")
        record = json.loads(self.location.read_text())
        workspace = Path(record["path"])
        parent = workspace.parent
        if (
            record.get("execution_id") != self.snapshot["execution_id"]
            or record.get("owner") != os.getuid()
            or workspace.name != "checkout"
            or not parent.name.startswith(prefix)
            or parent.parent.resolve() != self.scratch_root
            or parent.is_symlink()
            or workspace.is_symlink()
        ):
            raise DeliveryHeld("Publication scratch ownership changed; no access or cleanup.")
        if allow_absent and not parent.exists():
            return workspace  # No filesystem access/deletion is authorized by this observation.
        if (
            not parent.is_dir()
            or parent.stat().st_uid != os.getuid()
            or record.get("parent_identity") != [parent.stat().st_dev, parent.stat().st_ino]
            or (workspace.exists() and workspace.stat().st_uid != os.getuid())
        ):
            raise DeliveryHeld("Publication scratch ownership changed; no access or cleanup.")
        size = sum(
            p.stat().st_size for p in parent.rglob("*") if p.is_file() and not p.is_symlink()
        )
        if size > 1 << 30:
            raise DeliveryHeld("Publication scratch exceeded the existing1GiB ceiling; retained.")
        return workspace

    def cleanup(self, commit):
        """Local read-only checks, then remove only the exact clean owned scratch.

        Publication permission may have closed. No remote/provider/credential helper
        is invoked, and unexpected local work is retained for inspection.
        """
        import subprocess
        from workbench.real_workspace import durable

        marker = self.root / "publication-scratch-cleaned.json"
        if marker.exists():
            return json.loads(marker.read_text()).get("commit") == commit
        workspace = self._owned_checkout(allow_absent=True)
        if not workspace.parent.exists():
            durable(
                marker,
                json.dumps(
                    dict(
                        execution_id=self.snapshot["execution_id"],
                        commit=commit,
                        local_only=True,
                        removed=False,
                        already_absent=True,
                    ),
                    sort_keys=True,
                ).encode(),
            )
            return True
        env = dict(
            PATH="/usr/bin:/bin",
            HOME="/nonexistent",
            GIT_CONFIG_GLOBAL="/dev/null",
            GIT_CONFIG_NOSYSTEM="1",
            GIT_TERMINAL_PROMPT="0",
        )

        def read(*args):
            result = subprocess.run(
                ["git", "-c", "core.hooksPath=/dev/null", *args],
                cwd=workspace,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            if result.returncode or len(result.stdout) > 4096:
                raise DeliveryHeld("Publication scratch cleanup verification failed; retained.")
            return result.stdout.decode().strip()

        if read("rev-parse", "HEAD") != commit or read(
            "status", "--porcelain=v1", "--untracked-files=all"
        ):
            raise DeliveryHeld("Publication scratch has unexpected local changes; retained.")
        # Claim is still checked immediately before deletion. Never follow a moved directory.
        if self._owned_checkout() != workspace:
            raise DeliveryHeld("Publication scratch changed during cleanup.")
        shutil.rmtree(workspace.parent)
        durable(
            marker,
            json.dumps(
                dict(
                    execution_id=self.snapshot["execution_id"],
                    commit=commit,
                    local_only=True,
                    removed=True,
                ),
                sort_keys=True,
            ).encode(),
        )
        return True

    def _call(self, argv, cwd=None, raw=False):
        if not self.permitted():
            raise DeliveryHeld("Current task and operational publication permission required.")
        if cwd is not None and Path(cwd) == self.workspace:
            if self._owned_checkout() != self.workspace:
                raise DeliveryHeld("Exact owned publication checkout required.")
        if self._module is None:
            if (
                PILOT.is_symlink()
                or PILOT.stat().st_uid != os.getuid()
                or hashlib.sha256(PILOT.read_bytes()).hexdigest() != PILOT_SHA
            ):
                raise DeliveryHeld("Inspected publisher helper identity changed.")
            spec = importlib.util.spec_from_file_location("_workbench_pilot_command_helpers", PILOT)
            module = importlib.util.module_from_spec(spec)
            old = list(sys.path)
            try:
                spec.loader.exec_module(module)
            finally:
                sys.path[:] = old
            self._module = module
        env, _, helper = self._module.cred()
        original = env or os.environ
        env = {
            k: v
            for k, v in original.items()
            if k
            in (
                "PATH",
                "HOME",
                "LANG",
                "LC_ALL",
                "GH_TOKEN",
                "GH_HOST",
                "NO_PROXY",
                "no_proxy",
                "HTTPS_PROXY",
                "https_proxy",
            )
        }
        env.update(
            GIT_TERMINAL_PROMPT="0",
            GH_PROMPT_DISABLED="1",
            GIT_CONFIG_GLOBAL="/dev/null",
            GIT_CONFIG_NOSYSTEM="1",
        )
        for key in ("WORKBENCH_LOCAL_KEY", "LLAMA_API_KEY", "WORKBENCH_REAL_EXECUTION_ROOT"):
            env.pop(key, None)
        if argv[:2] == ["git", "push"]:
            argv = ["git", *helper, "-c", "core.hooksPath=/dev/null", *argv[1:]]
        elif argv[0] == "git":
            argv = ["git", "-c", "core.hooksPath=/dev/null", *argv[1:]]
        try:
            if raw:
                result = self._module.subprocess.run(
                    argv,
                    cwd=str(cwd) if cwd else None,
                    stdout=self._module.subprocess.PIPE,
                    stderr=self._module.subprocess.DEVNULL,
                    timeout=60,
                    env=env,
                    check=False,
                )
                rc, out = result.returncode, result.stdout
            else:
                rc, out, _ = self._module.sh(argv, cwd=str(cwd) if cwd else None, timeout=60, env=env)
        except TimeoutExpired:
            raise RuntimeError("Publisher command timed out; its result is unconfirmed") from None
        # Never persist arbitrary Git/GH stderr, which may contain credentials.
        if rc or len(out if isinstance(out, bytes) else out.encode()) > 4 << 20:
            raise RuntimeError("Publisher command did not return a bounded successful response")
        if self.location.exists():
            self._owned_checkout()  # Enforce the same bounded scratch after fixed commands.
        return out if raw else out.strip()

    def _url(self, snapshot):
        if snapshot["repository"] != "Twich-Team/TwichUI" or snapshot["base_branch"] != "main":
            raise DeliveryHeld("Repository/base are not the inspected publication destination.")
        if not re.fullmatch(r"maintenance/workbench-[0-9a-f]{20}", snapshot["branch"]):
            raise DeliveryHeld("Only this delivery new maintenance branch may be used.")
        return "https://github.com/" + snapshot["repository"] + ".git"

    def base_head(self, snapshot):
        rows = self._call(
            ["git", "ls-remote", self._url(snapshot), "refs/heads/" + snapshot["base_branch"]]
        ).split()
        return rows[0] if rows else None

    def branch_head(self, snapshot):
        rows = self._call(
            ["git", "ls-remote", self._url(snapshot), "refs/heads/" + snapshot["branch"]]
        ).split()
        return rows[0] if rows else None

    def prepare(self, snapshot, result):
        from workbench.session_authorship import authorship_valid

        contract = (
            snapshot.get("task_budget", {})
            .get("offer", {})
            .get("snapshot", {})
            .get("specification", {})
            .get("contract", {})
        )
        if result.get("inference_ran") is not True and not authorship_valid(contract, result):
            raise DeliveryHeld(
                "A real model implementation is required; controls cannot be published."
            )
        self._url(snapshot)
        spec = snapshot["task_budget"]["offer"]["snapshot"]["specification"]
        source = Path(spec["contract"]["source"])
        source_bytes = sum(
            p.stat().st_size for p in source.rglob("*") if p.is_file() and not p.is_symlink()
        )
        if source_bytes + (1 << 20) > 1 << 30:
            raise DeliveryHeld(
                "Pinned publication source does not fit the existing scratch ceiling."
            )
        self.workspace = self._owned_checkout(create=True)
        if not self.workspace.exists():
            self._call(
                [
                    "git",
                    "-c",
                    "protocol.allow=never",
                    "-c",
                    "protocol.file.allow=always",
                    "clone",
                    "--local",
                    "--no-hardlinks",
                    "--no-recurse-submodules",
                    "--quiet",
                    str(source),
                    str(self.workspace),
                ]
            )
        if self.workspace.is_symlink() or self.workspace.stat().st_uid != os.getuid():
            raise DeliveryHeld("Owned isolated publication checkout required.")

        def git(*args):
            return self._call(["git", *args], self.workspace)

        head = git("rev-parse", "HEAD")
        paths = sorted(f["path"] for f in result["files"])
        if any(p not in spec["contract"]["paths"] for p in paths):
            raise DeliveryHeld("Unexpected publication file scope.")
        patch = result["manifest"]["patch_sha256"]
        if head == snapshot["base"]:
            dirty = bool(git("status", "--porcelain=v1", "--untracked-files=all"))
            if dirty:
                # An interruption after apply is recoverable ONLY for the exact
                # frozen patch in this owned branch. Never reset unknown work.
                if not self.prepared_patch_matches(snapshot, result):
                    raise DeliveryHeld(
                        "Publication checkout has unrelated or partial local changes; no reset."
                    )
            else:
                if git("rev-parse", "--abbrev-ref", "HEAD") != snapshot["branch"]:
                    git("checkout", "--quiet", "-b", snapshot["branch"], snapshot["base"])
                git("remote", "set-url", "origin", self._url(snapshot))
                grant_root = Path(snapshot["evidence_root"])
                candidate = grant_root / "candidates" / snapshot["candidate_id"]
                if hashlib.sha256((candidate / "changes.patch").read_bytes()).hexdigest() != patch:
                    raise DeliveryHeld("Frozen patch integrity failed before preparing the commit.")
                git("apply", "--check", str(candidate / "changes.patch"))
                git("apply", str(candidate / "changes.patch"))
                if not self.prepared_patch_matches(snapshot, result):
                    raise DeliveryHeld("Prepared patch changed unrelated paths.")
            identity = spec["identity"]
            git("config", "--local", "user.name", identity["name"])
            git("config", "--local", "user.email", identity["email"])
            # Keep the message outside the checkout so it cannot enter the patch.
            message = self.root / ("message-" + snapshot["execution_id"] + ".txt")
            data = (
                snapshot["preview"]["title"]
                + "\n\nWorkbench task "
                + snapshot["preview"]["task_id"]
                + "\n"
            )
            if "co-authored-by" in data.lower():
                raise DeliveryHeld("Unexpected commit trailer.")
            if message.exists() and message.read_text() != data:
                raise DeliveryHeld("Commit message changed.")
            if not message.exists():
                message.write_text(data)
            git("add", "--", *paths)
            git("commit", "--quiet", "-F", str(message))
            head = git("rev-parse", "HEAD")
        # A crash after commit but before receipt is recovered by inspecting the
        # existing exact commit. Never apply/commit the same patch again.
        if (
            git("rev-parse", "HEAD^") != snapshot["base"]
            or git("rev-parse", "--abbrev-ref", "HEAD") != snapshot["branch"]
        ):
            raise DeliveryHeld("Prepared commit/base/branch mismatch.")
        changed = git("diff", "--name-only", snapshot["base"], "HEAD").splitlines()
        if sorted(changed) != paths or git("status", "--porcelain=v1", "--untracked-files=all"):
            raise DeliveryHeld("Prepared commit scope or working-tree integrity failed.")
        for path in paths:
            expected = (
                Path(snapshot["evidence_root"])
                / "candidates"
                / snapshot["candidate_id"]
                / "files"
                / path
            ).read_bytes()
            # Text-only bounded Lua recipe; preserve exact UTF8/trailing whitespace.
            actual = self._call(["git", "show", "HEAD:" + path], self.workspace, raw=True)
            if actual != expected:
                raise DeliveryHeld("Prepared commit differs from the frozen file.")
        return head

    def prepared_patch_matches(self, snapshot, result):
        """Read-only exact-applied recovery proof; NUL framing preserves status spaces."""
        self.workspace = self._owned_checkout()

        def git(*args, raw=False):
            return self._call(["git", *args], self.workspace, raw=raw)

        if (
            git("rev-parse", "HEAD") != snapshot["base"]
            or git("rev-parse", "--abbrev-ref", "HEAD") != snapshot["branch"]
        ):
            return False
        status = git("status", "--porcelain=v1", "-z", "--untracked-files=all", raw=True)
        entries = status.split(b"\0")
        if not status or entries[-1] != b"":
            return False
        try:
            # Only unstaged modifications produced by git apply are supported.
            if any(not row.startswith(b" M ") for row in entries[:-1]):
                return False
            paths = sorted(row[3:].decode("utf-8") for row in entries[:-1])
        except UnicodeDecodeError:
            return False
        expected = sorted(f["path"] for f in result["files"])
        if paths != expected or any(
            p
            not in snapshot["task_budget"]["offer"]["snapshot"]["specification"]["contract"][
                "paths"
            ]
            for p in paths
        ):
            return False
        patch = git(
            "diff", "--no-ext-diff", "--no-textconv", "--binary", snapshot["base"], raw=True
        )
        if hashlib.sha256(patch).hexdigest() != result["manifest"]["patch_sha256"]:
            return False
        for name in paths:
            file = self.workspace / name
            if (
                file.is_symlink()
                or not file.is_file()
                or hashlib.sha256(file.read_bytes()).hexdigest() != result["manifest"]["tree"][name]
            ):
                return False
        return True

    def push(self, snapshot, commit):
        self._url(snapshot)
        if self._call(["git", "rev-parse", "HEAD"], self.workspace) != commit:
            raise DeliveryHeld("Publication checkout no longer matches its recorded commit.")
        if self._call(
            ["git", "remote", "get-url", "--push", "origin"], self.workspace
        ) != self._url(snapshot):
            raise DeliveryHeld("Publication remote differs from the inspected destination.")
        try:
            self._call(
                [
                    "git",
                    "push",
                    "--force-with-lease=refs/heads/" + snapshot["branch"] + ":",
                    "origin",
                    "refs/heads/" + snapshot["branch"] + ":refs/heads/" + snapshot["branch"],
                ],
                self.workspace,
            )
        except RuntimeError:
            raise UncertainWrite("push") from None

    def find_pr(self, snapshot, commit):
        rows = json.loads(
            self._call(
                [
                    "gh",
                    "pr",
                    "list",
                    "-R",
                    snapshot["repository"],
                    "--head",
                    snapshot["branch"],
                    "--state",
                    "all",
                    "--limit",
                    "2",
                    "--json",
                    "url,isDraft,baseRefName,headRefName,headRefOid,state,body,isCrossRepository",
                ]
            )
        )
        if len(rows) > 1:
            raise DeliveryHeld("Multiple PRs found for this branch; no new PR.")
        return rows[0] if rows else None

    def create_pr(self, snapshot, commit):
        body = self.root / ("body-" + snapshot["execution_id"] + ".md")
        if body.exists() and body.read_text() != snapshot["preview"]["body"]:
            raise DeliveryHeld("PR body changed.")
        if not body.exists():
            body.write_text(snapshot["preview"]["body"])
        try:
            self._call(
                [
                    "gh",
                    "pr",
                    "create",
                    "-R",
                    snapshot["repository"],
                    "--draft",
                    "--base",
                    snapshot["base_branch"],
                    "--head",
                    snapshot["branch"],
                    "--title",
                    snapshot["preview"]["title"],
                    "--body-file",
                    str(body),
                ],
                self.workspace,
            )
        except RuntimeError:
            raise UncertainWrite("pr") from None

    def valid_pr(self, snapshot, commit, value):
        return (
            value.get("isDraft") is True
            and value.get("isCrossRepository") is False
            and value.get("state") == "OPEN"
            and value.get("baseRefName") == snapshot["base_branch"]
            and value.get("headRefName") == snapshot["branch"]
            and value.get("headRefOid") == commit
            and value.get("body") == snapshot["preview"]["body"]
            and re.fullmatch(
                r"https://github.com/Twich-Team/TwichUI/pull/[1-9][0-9]{0,9}", value.get("url", "")
            )
            is not None
        )
