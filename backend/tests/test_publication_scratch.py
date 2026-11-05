"""NON-MODEL local scratch controls. No Git commits/network/provider/credentials."""

import json
import shutil
from pathlib import Path
from uuid import uuid4
import pytest
from workbench.publication_adapter import PilotGitHub
from workbench.real_publication import DeliveryHeld


@pytest.fixture
def client(tmp_path):
    root = tmp_path / "evidence"
    root.mkdir()
    value = PilotGitHub(root, dict(execution_id=str(uuid4())))
    value.permitted = lambda: True
    yield value
    if value.location.exists():
        data = json.loads(value.location.read_text())
        parent = Path(data["path"]).parent
        if (
            parent.exists()
            and not parent.is_symlink()
            and parent.name.startswith("workbench-publish-")
        ):
            shutil.rmtree(parent)


def test_checkout_is_outside_evidence_and_recovered_by_exact_identity(client):
    workspace = client._owned_checkout(create=True)
    assert not workspace.is_relative_to(client.root) and workspace.parent.name.startswith(
        "workbench-publish-"
    )
    workspace.mkdir()
    (workspace / "bounded.txt").write_text("NON-MODEL scratch")
    recovered = PilotGitHub(client.root, client.snapshot)
    assert recovered.workspace == workspace and recovered._owned_checkout() == workspace
    assert len(list(client.root.iterdir())) == 1


def test_no_permission_creates_no_checkout_or_marker(client):
    client.permitted = lambda: False
    with pytest.raises(DeliveryHeld, match="permission"):
        client._owned_checkout(create=True)
    assert not client.location.exists()


def test_changed_inode_symlink_or_execution_cannot_claim_another_checkout(client, tmp_path):
    workspace = client._owned_checkout(create=True)
    data = json.loads(client.location.read_text())
    data["parent_identity"][1] += 1
    client.location.write_text(json.dumps(data))
    with pytest.raises(DeliveryHeld, match="ownership"):
        client._owned_checkout()
    data["parent_identity"][1] -= 1
    client.location.write_text(json.dumps(data))
    workspace.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(DeliveryHeld, match="ownership"):
        client._owned_checkout()
    workspace.unlink()
    data["execution_id"] = str(uuid4())
    client.location.write_text(json.dumps(data))
    with pytest.raises(DeliveryHeld, match="ownership"):
        client._owned_checkout()


def test_missing_directory_can_be_recorded_as_absent_but_never_recreated(client):
    workspace = client._owned_checkout(create=True)
    workspace.parent.rmdir()
    recovered = PilotGitHub(client.root, client.snapshot)
    with pytest.raises(DeliveryHeld):
        recovered._owned_checkout(create=True)
    assert recovered.cleanup("b" * 40)
    proof = json.loads((client.root / "publication-scratch-cleaned.json").read_text())
    assert proof["already_absent"] and not proof["removed"]
    assert PilotGitHub(client.root, client.snapshot).cleanup("b" * 40)


def test_cleanup_refuses_unverified_local_work_without_deleting(client):
    workspace = client._owned_checkout(create=True)
    workspace.mkdir()
    (workspace / "local.txt").write_text("Retain unexpected work")
    with pytest.raises(DeliveryHeld, match="cleanup verification failed"):
        client.cleanup("b" * 40)
    assert (workspace / "local.txt").read_text() == "Retain unexpected work"


def test_clean_verified_checkout_cleanup_preserves_unrelated_paths(client, tmp_path, monkeypatch):
    from types import SimpleNamespace
    import subprocess

    workspace = client._owned_checkout(create=True)
    workspace.mkdir()
    (workspace / "candidate.txt").write_text("NON-MODEL owned scratch")
    unrelated = tmp_path / "unrelated.txt"
    unrelated.write_text("preserve")

    def readonly(argv, **kwargs):
        assert (
            kwargs["cwd"] == workspace
            and "GH_TOKEN" not in kwargs["env"]
            and "LLAMA_API_KEY" not in kwargs["env"]
        )
        assert argv[:3] == ["git", "-c", "core.hooksPath=/dev/null"]
        return SimpleNamespace(
            returncode=0, stdout=(("b" * 40 + "\n").encode() if "rev-parse" in argv else b"")
        )

    monkeypatch.setattr(subprocess, "run", readonly)
    assert client.cleanup("b" * 40) and not workspace.parent.exists()
    assert unrelated.read_text() == "preserve"
    assert json.loads((client.root / "publication-scratch-cleaned.json").read_text())["removed"]
