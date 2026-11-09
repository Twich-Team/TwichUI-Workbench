"""Reuse pre-model observations, never a model result or its permission."""

import json

import pytest

from test_verification_profiles import (
    setup,
    controls,
    boundary,
    ControlledLuaRuntime,
    TEST,
    GOOD_TEST,
)
from workbench import verification_profiles as profiles
from workbench.real_workspace import Runtime, durable
from test_verification_profiles import git


class InventoryRuntime(ControlledLuaRuntime):
    # Reopening retained evidence uses the production Runtime inventory. Keep
    # this fixture's real commands consistent with that inventory as well.
    expected_suite = Runtime.expected_suite


def prepared(tmp_path):
    seed = setup(tmp_path)
    source = tmp_path / "source"
    runner = source / "tests/run.sh"
    runner.write_text(runner.read_text().replace("echo '1 passed, 0 failed'", '''for t in test_sv_inspect.py test_release_tools.py test_wizard_art.py; do
  python3 "$t"
  echo "ok   $t"
done
echo '4 passed, 0 failed' '''))
    for name in ["test_sv_inspect.py", "test_release_tools.py", "test_wizard_art.py"]:
        (source / "tests" / name).write_text("assert 1 + 1 == 2\n")
    git(source, "add", ".")
    git(source, "-c", "user.name=Fixture", "-c", "user.email=fixture@invalid", "commit", "-qm", "Fixture suite inventory")
    recipe = dict(seed.recipe)
    recipe["contract"] = dict(recipe["contract"], base=git(source, "rev-parse", "HEAD").strip())
    root = tmp_path / "certified"
    root.mkdir(mode=0o700)
    durable(root / "recipe.json", json.dumps(recipe).encode())
    durable(root / "recipe-oracle.lua", seed.oracle_source.read_bytes())
    previous = InventoryRuntime(root)
    previous.prepare()
    previous.root.chmod(0o700)
    patches = controls(previous)
    profiles.prepare(previous, patches)
    previous.record_controls(boundary(previous))
    root = tmp_path / "fresh-run"
    root.mkdir(mode=0o700)
    for name in ["recipe.json", "recipe-oracle.lua"]:
        durable(root / name, (previous.root / name).read_bytes())
    current = InventoryRuntime(root)
    current.prepare()
    return previous, current, patches


def test_reuse_keeps_original_commands_and_requires_a_new_boundary(tmp_path):
    previous, current, patches = prepared(tmp_path)
    original = (previous.root / "profile-evidence.json").read_bytes()
    assert profiles.reuse_controls(current, previous.root, patches)
    assert not current.controls_valid()
    assert not (current.root / "profile-evidence.json").exists()
    assert profiles.control_certificate(current) == profiles.control_certificate(previous)
    current.record_controls(boundary(current))
    assert current.controls_valid()
    # Model edits belong in the fresh copy, not the immutable source preparation.
    (current.workspace / TEST).write_text((current.workspace / TEST).read_text() + GOOD_TEST)
    assert current.controls_valid()
    assert (previous.root / "profile-evidence.json").read_bytes() == original
    assert not list(current.root.glob("ownership-*"))


@pytest.mark.parametrize(
    "fault",
    [
        "recipe",
        "oracle",
        "scope",
        "dirty-current",
        "source-manifest",
        "source-evidence",
        "source-boundary",
        "patch",
        "nested",
        "source-link",
    ],
)
def test_changed_or_untrustworthy_preparation_cannot_supply_reused_controls(tmp_path, fault):
    previous, current, patches = prepared(tmp_path)
    source = previous.root
    if fault == "recipe":
        value = json.loads((current.root / "recipe.json").read_text())
        value["instruction"] += " changed"
        (current.root / "recipe.json").write_text(json.dumps(value))
    elif fault == "oracle":
        current.oracle_source.write_text('print("unverified")')
    elif fault == "scope":
        current.recipe["contract"]["paths"].append("tests/test_other.lua")
    elif fault == "dirty-current":
        (current.workspace / TEST).write_text("assert(true)\n")
    elif fault == "source-manifest":
        previous.initial.write_text("{}")
    elif fault == "source-evidence":
        (previous.root / "profile-evidence/original/check-00.json").write_text("{}")
    elif fault == "source-boundary":
        (previous.root / "boundary-proof.json").write_text("{}")
    elif fault == "patch":
        patches["vacuous"] = "changed control"
    elif fault == "nested":
        (previous.root / "profile-reference.json").write_text("{}")
    else:
        source = tmp_path / "alias"
        source.symlink_to(previous.root, target_is_directory=True)
    with pytest.raises((ValueError, KeyError)):
        profiles.reuse_controls(current, source, patches)
    assert not (current.root / "controls-passed.json").exists()


def test_reference_and_fresh_boundary_are_bound_after_recording(tmp_path):
    previous, current, patches = prepared(tmp_path)
    profiles.reuse_controls(current, previous.root, patches)
    current.record_controls(boundary(current))
    assert current.controls_valid()
    reference = current.root / "profile-reference.json"
    value = json.loads(reference.read_text())
    value["source"] = str(tmp_path / "other")
    reference.write_text(json.dumps(value))
    assert not current.controls_valid()


def test_reuse_does_not_accept_an_unsafe_fresh_boundary(tmp_path):
    previous, current, patches = prepared(tmp_path)
    profiles.reuse_controls(current, previous.root, patches)
    proof = tmp_path / "bad-boundary.json"
    proof.write_text(
        json.dumps(
            dict(checks={"current": dict(usage=dict(cgroup_removed=False, leaked_after_kill=True))})
        )
    )
    with pytest.raises(ValueError, match="Boundary"):
        current.record_controls(proof)
    assert not current.controls_valid()


def test_legacy_template_without_a_source_still_requires_full_preparation(tmp_path):
    _, current, patches = prepared(tmp_path)
    assert not profiles.reuse_controls(current, None, patches)
    assert not current.controls_valid()


def test_retired_preparation_copy_is_not_required_for_frozen_evidence(tmp_path):
    import shutil

    previous, current, patches = prepared(tmp_path)
    # Normal application retention removes this disposable copy after freezing.
    # No commands in the new execution may read it or copy candidate results.
    shutil.rmtree(previous.workspace.parent)
    assert profiles.reuse_controls(current, previous.root, patches)
    current.record_controls(boundary(current))
    assert current.controls_valid()


@pytest.mark.parametrize("value", [None, [], {}, {"source": 7}, {"source": None}])
def test_malformed_reference_cannot_revive_valid_controls(tmp_path, value):
    previous, current, patches = prepared(tmp_path)
    profiles.reuse_controls(current, previous.root, patches)
    current.record_controls(boundary(current))
    (current.root / "profile-reference.json").write_text(json.dumps(value))
    assert not current.controls_valid()


@pytest.mark.parametrize("name", ["profile-evidence.json", "controls-passed.json", "boundary-proof.json"])
def test_source_evidence_is_revalidated_after_reference_creation(tmp_path, name):
    previous, current, patches = prepared(tmp_path)
    profiles.reuse_controls(current, previous.root, patches)
    current.record_controls(boundary(current))
    (previous.root / name).write_text("{}")
    assert not current.controls_valid()
