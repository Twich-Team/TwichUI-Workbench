"""Actual local Git adapter preparation; no network, credentials, push or real PR."""

import hashlib
import subprocess
from uuid import uuid4
import pytest
from workbench.publication_adapter import PilotGitHub
from workbench.real_publication import DeliveryHeld


def git(root, *args, raw=False):
    r = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *args], cwd=root, capture_output=True, check=True
    )
    return r.stdout if raw else r.stdout.decode().strip()


class LocalOnly(PilotGitHub):
    interrupt_apply = False

    def _call(self, argv, cwd=None, raw=False):
        assert argv[0] == "git" and argv[1] not in ("push", "ls-remote", "fetch", "pull")
        value = git(cwd or self.root, *argv[1:], raw=raw)
        if self.interrupt_apply and argv[1] == "apply" and "--check" not in argv:
            self.interrupt_apply = False
            raise OSError("NON-MODEL interruption after local apply")
        return value


@pytest.fixture
def prepared(tmp_path):
    source = tmp_path / "base"
    source.mkdir()
    git(source, "init", "-q")
    git(source, "config", "user.name", "NON-MODEL")
    git(source, "config", "user.email", "control@example.invalid")
    path = "tests/test_example.lua"
    file = source / path
    file.parent.mkdir()
    file.write_text("-- base\n")
    git(source, "add", ".")
    git(source, "commit", "-qm", "NON-MODEL base")
    base = git(source, "rev-parse", "HEAD")
    final = b"-- base\nassert(true)\n"
    file.write_bytes(final)
    patch = git(source, "diff", "--no-ext-diff", "--no-textconv", "--binary", base, raw=True)
    file.write_text("-- base\n")
    evidence = tmp_path / "evidence"
    candidate = "a" * 64
    c = evidence / "candidates" / candidate
    c.mkdir(parents=True)
    (c / "changes.patch").write_bytes(patch)
    target = c / "files" / path
    target.parent.mkdir(parents=True)
    target.write_bytes(final)
    root = tmp_path / "publication"
    root.mkdir()
    spec = dict(
        contract=dict(source=str(source), paths=[path]),
        identity=dict(name="NON-MODEL", email="control@example.invalid"),
    )
    snapshot = dict(
        execution_id=str(uuid4()),
        repository="Twich-Team/TwichUI",
        base_branch="main",
        base=base,
        branch="maintenance/workbench-" + "b" * 20,
        evidence_root=str(evidence),
        candidate_id=candidate,
        task_budget=dict(offer=dict(snapshot=dict(specification=spec))),
        preview=dict(title="NON-MODEL test", task_id="NON-MODEL"),
    )
    result = dict(
        inference_ran=True,
        files=[dict(path=path)],
        manifest=dict(
            patch_sha256=hashlib.sha256(patch).hexdigest(),
            tree={path: hashlib.sha256(final).hexdigest()},
        ),
    )
    adapter = LocalOnly(root, snapshot)
    adapter.permitted = lambda: True
    yield adapter, snapshot, result
    import shutil

    if adapter.location.exists():
        owned = adapter._owned_checkout()
        shutil.rmtree(owned.parent)


def test_real_porcelain_spaces_are_preserved_and_exact_commit_retry_is_idempotent(prepared):
    adapter, snapshot, result = prepared
    head = adapter.prepare(snapshot, result)
    assert adapter.prepare(snapshot, result) == head
    assert git(adapter.workspace, "rev-list", "--count", snapshot["base"] + "..HEAD") == "1"
    assert (
        git(adapter.workspace, "diff", "--name-only", snapshot["base"], "HEAD")
        == "tests/test_example.lua"
    )


def test_apply_interruption_resumes_only_exact_frozen_patch(prepared):
    adapter, snapshot, result = prepared
    adapter.interrupt_apply = True
    with pytest.raises(OSError):
        adapter.prepare(snapshot, result)
    status = git(adapter.workspace, "status", "--porcelain=v1", "-z", raw=True)
    assert status == b" M tests/test_example.lua\0"
    assert adapter.prepared_patch_matches(snapshot, result)
    head = adapter.prepare(snapshot, result)
    assert git(adapter.workspace, "rev-list", "--count", snapshot["base"] + ".." + head) == "1"


def test_unrelated_partial_work_is_held_without_reset(prepared):
    adapter, snapshot, result = prepared
    adapter.interrupt_apply = True
    with pytest.raises(OSError):
        adapter.prepare(snapshot, result)
    extra = adapter.workspace / "unrelated.txt"
    extra.write_text("keep me")
    assert not adapter.prepared_patch_matches(snapshot, result)
    with pytest.raises(DeliveryHeld, match="unrelated or partial"):
        adapter.prepare(snapshot, result)
    assert extra.read_text() == "keep me"
