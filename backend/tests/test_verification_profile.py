"""No generation: model/API arguments cannot obtain trusted verification limits."""

from pathlib import Path
import pytest
from workbench import real_confine
from workbench.verification_process import VerificationProcess


@pytest.mark.parametrize(
    "change",
    [
        dict(fsize=128 << 20),
        dict(pids=128),
        dict(memory=7 << 30),
        dict(cpu_pct=300),
        dict(nofile=4096),
    ],
)
def test_untrusted_commands_cannot_raise_any_limit(tmp_path, change):
    value = real_confine.run(["true"], str(tmp_path), 1, limits=change)
    assert value["status"] == "error" and value["rc"] is None


@pytest.mark.parametrize("forgery", [True, {}, dict(command=("true",), root="/tmp")])
def test_json_boolean_is_not_verification_capability(tmp_path, forgery):
    assert (
        real_confine.run(["true"], str(tmp_path), 1, private_loopback=True, verification=forgery)[
            "status"
        ]
        == "error"
    )


def test_verification_capability_is_bound_to_exact_owned_command_and_directory(tmp_path):
    root = tmp_path / "workbench-verify-owned" / "source"
    root.mkdir(parents=True)
    proof = VerificationProcess(("true",), str(root))
    proof.validate(["true"], str(root), [str(root)])
    with pytest.raises(ValueError):
        proof.validate(["false"], str(root), [str(root)])
    with pytest.raises(ValueError):
        proof.validate(["true"], str(tmp_path), [str(root)])
    with pytest.raises(ValueError):
        proof.validate(["true"], str(root), ["/"])
    assert real_confine.run(["true"], str(root), 1, verification=proof)["status"] == "error"


def test_verification_child_receives_no_ambient_credentials(tmp_path, monkeypatch):
    from workbench.application_checks import configuration

    monkeypatch.setenv("LLAMA_API_KEY", "NON-MODEL-DUMMY")
    monkeypatch.setenv("GH_TOKEN", "NON-MODEL-DUMMY")
    monkeypatch.setenv("WORKBENCH_LOCAL_KEY", "NON-MODEL-DUMMY")
    env, _, _ = configuration(tmp_path, Path(__file__).resolve().parents[2])
    assert not {"LLAMA_API_KEY", "GH_TOKEN", "WORKBENCH_LOCAL_KEY"} & env.keys()


def test_forged_inherited_namespace_evidence_cannot_skip_boundary_assertions(monkeypatch, tmp_path):
    import json
    import os
    from test_real_network import (
        test_verification_loopback_is_private_and_host_ipc_denied as check_boundary,
    )

    monkeypatch.setenv(
        "WORKBENCH_VERIFY_PARENT_MARKER",
        json.dumps(dict(netns=os.readlink("/proc/self/ns/net"), port=1)),
    )
    with pytest.raises(AssertionError):
        check_boundary(tmp_path)
