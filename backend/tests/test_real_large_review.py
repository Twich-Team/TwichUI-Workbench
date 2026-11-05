"""NON-MODEL exact production freezer with a legitimate >32KiB test file."""

import json
import pytest
from workbench.candidate_review import compare, INPUT_LIMIT
from workbench.real_workspace import Runtime, sha, tree


def test_verified_real_input_allowance_is_explicit_and_default_stays_32k():
    before = b"-- padding\n" * 4500
    after = before + b'Check(true, "regression")\n'
    assert len(before) > INPUT_LIMIT
    assert compare("tests/test_example.lua", before, after)["kind"] == "unavailable"
    value = compare("tests/test_example.lua", before, after, input_limit=65536)
    assert value["kind"] == "modified" and value["additions"] == 1 and not value["truncated"]
    assert "+Check(true" in value["diff"]
    assert (
        compare("tests/test_example.lua", before, b"x" * 65537, input_limit=65536)["kind"]
        == "unavailable"
    )
    assert compare("../escape.lua", before, after, input_limit=65536)["kind"] == "unavailable"
    assert compare("tests/test_example.lua", b"\0", after, input_limit=65536)["kind"] == "binary"
    with pytest.raises(ValueError):
        compare("tests/test_example.lua", before, after, input_limit=1 << 20)


def test_actual_freezer_keeps_large_real_diff_readable_and_integrity_bound(tmp_path):
    # Real compare/freezer/artifact identity. Scripted check status is not real verification.
    import subprocess

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", *args],
            cwd=workspace,
            check=True,
            capture_output=True,
        )

    git("init", "-q")
    git("config", "user.name", "NON-MODEL")
    git("config", "user.email", "control@example.invalid")
    p = workspace / "tests/test_large.lua"
    p.parent.mkdir()
    p.write_text("-- padding\n" * 4500)
    git("add", ".")
    git("commit", "-qm", "NON-MODEL local test base")
    base = git("rev-parse", "HEAD").stdout.decode().strip()
    p.write_text(p.read_text() + 'Check(true, "regression")\n')
    root = tmp_path / "real-run"
    root.mkdir()
    oracle = root / "oracle.lua"
    oracle.write_text("-- NON-MODEL control")
    runtime = object.__new__(Runtime)
    runtime.root = root
    runtime.workspace = workspace
    runtime.base = base
    runtime.paths = ("tests/test_large.lua",)
    runtime.recipe = {"NON_MODEL": True}
    runtime._context_sha = None
    runtime.oracle_source = oracle
    runtime.initial = root / "initial.json"
    runtime.initial.write_text(
        json.dumps(
            {**tree(workspace), "tests/test_large.lua": sha(("-- padding\n" * 4500).encode())}
        )
    )
    runtime.expected_suite = lambda: []
    checks = [dict(status="pass", rc=0, command=["CONTROL"], out="0 passed, 0 failed\n")]
    runtime._last_candidate_check = dict(
        tree=tree(workspace), feedback=dict(status="pass"), checks=checks, evidence="NON-MODEL"
    )
    # Inventory independently tested elsewhere; this test targets freezer comparison integrity only.
    from unittest.mock import patch

    with patch("workbench.real_workspace.suite_inventory", return_value=True):
        result = runtime.freeze_candidate(
            "NON-MODEL-request",
            "NON-MODEL-fence",
            lambda: True,
            dict(correction_count=0, read_input_errors=0),
            dict(usage={"status": "unavailable"}),
        )
    assert result["verification"] == "passed" and runtime.verify_frozen(result)
    assert result["files"][0]["additions"] == 1 and result["files"][0]["diff"]
    (root / "candidates" / result["candidate_id"] / "files/tests/test_large.lua").write_text(
        "changed"
    )
    assert not runtime.verify_frozen(result)
