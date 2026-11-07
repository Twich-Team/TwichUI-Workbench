"""Exercise the timing configuration used by the actual delegated controller."""

import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile

import pytest
from workbench.bug_investigation import INVESTIGATION_SECONDS

ROOT = Path(__file__).resolve().parents[2]


def test_controller_phases_and_cleanup_fit_before_dispatch():
    from project_env import environment

    script = "import {caseTiming} from './scripts/acceptance_timing.mjs'; console.log(JSON.stringify([caseTiming(true),caseTiming(false)]));"
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        env=environment(),
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    bug, ordinary = json.loads(result.stdout)
    for cfg in [bug, ordinary]:
        assert (
            cfg["planningMinutes"]
            + cfg["reviewMinutes"]
            + cfg["preparationMinutes"]
            + cfg["runMinutes"]
            + 1
            <= cfg["caseMinutes"]
        )
        assert cfg["caseMinutes"] + cfg["cleanupMinutes"] <= cfg["operationMinutes"] == 150
        assert cfg["cleanupMinutes"] >= 10
    assert bug["planningMinutes"] * 60 == INVESTIGATION_SECONDS + 60
    assert ordinary["planningMinutes"] == 2.5


def test_disposable_source_renewal_retains_original_and_rejects_unsafe_paths(store):
    from reviewed_offer_fixture import fresh_controlled_source
    from test_verification_profiles import PRODUCTION, CORRECT

    with tempfile.TemporaryDirectory(prefix="workbench-e2e-") as name:
        root = Path(name)
        database = root / "test.sqlite3"
        with sqlite3.connect(store.url.database) as original, sqlite3.connect(database) as target:
            original.backup(target)
        research = root / "reviewed-research"
        source = research / "source"
        source.mkdir(parents=True)
        (source / "closed-fixture.txt").write_text("RETAIN ORIGINAL")
        fresh = fresh_controlled_source(database)
        assert (fresh / PRODUCTION).read_text() == CORRECT
        retained = list(research.glob("retained-source-*"))
        assert (
            len(retained) == 1
            and (retained[0] / "closed-fixture.txt").read_text() == "RETAIN ORIGINAL"
        )
        # A different database path cannot turn this into arbitrary file management.
        with pytest.raises(SystemExit):
            fresh_controlled_source(Path(store.url.database))
        import os

        fresh.rename(research / "safe-fixture")
        os.symlink(research / "safe-fixture", fresh)
        with pytest.raises(ValueError):
            fresh_controlled_source(database)
        assert (research / "safe-fixture" / PRODUCTION).read_text() == CORRECT
