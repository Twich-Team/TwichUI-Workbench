"""NON-MODEL package budget controls, with no live lease or inference."""

import json
import time
from types import SimpleNamespace

import pytest

from workbench.real_package import Package, package_for


@pytest.fixture
def package(tmp_path):
    root = tmp_path / "chronicle-completion-test"
    root.mkdir()
    started = time.time() - 1
    spec = dict(
        started_at=started,
        deadline=started + 14400,
        max_cycles=3,
        max_probes=3,
        max_attempts=3,
        max_generated_tokens=72000,
        reserved_tokens_per_cycle=24000,
        original_attempt_token_ceiling=24000,
        original_turns=20,
    )
    (root / "BUDGET_BASELINE.json").write_text(json.dumps(spec))
    return Package(root)


def cycle(package, index):
    root = package.root.parent / f"chronicle-lease-{package.root.name}-{index:02}"
    root.mkdir()
    return root


def finished(root):
    (root / "lease-controller-result.json").write_text(
        json.dumps(dict(restoration=dict(restored=True)))
    )


def test_three_allocations_no_fourth_and_no_reset_after_failure(package):
    for index in range(1, 4):
        root = cycle(package, index)
        row = package.reserve(root)
        assert row["index"] == index and row["generated_capacity"] == 24000
        assert package_for(root).verify(root)["cycle"] == index
        with pytest.raises(ValueError):
            package.reserve(root)
        finished(root)  # test double; no real operation occurred
    with pytest.raises(ValueError, match="exhausted"):
        package.reserve(cycle(package, 4))
    assert sum(r["generated_capacity"] for r in package.reservations()) == 72000


def test_new_two_hour_two_cycle_authorization_is_separate_and_bounded(package):
    data = json.loads(package.spec.read_text())
    data.update(
        deadline=data["started_at"] + 7200,
        max_cycles=2,
        max_probes=2,
        max_attempts=2,
        max_generated_tokens=48000,
    )
    package.spec.write_text(json.dumps(data))
    bounded = Package(package.root)
    for i in (1, 2):
        root = cycle(bounded, i)
        bounded.reserve(root)
        finished(root)
    with pytest.raises(ValueError, match="exhausted"):
        bounded.reserve(cycle(bounded, 3))
    data["max_probes"] = 3
    package.spec.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="ceilings"):
        Package(package.root)


def test_unrestored_or_running_cycle_blocks_competing_cycle(package):
    first = cycle(package, 1)
    package.reserve(first)
    second = cycle(package, 2)
    with pytest.raises(ValueError, match="reconciled"):
        package.reserve(second)
    assert len(package.reservations()) == 1


def test_baseline_tamper_or_cycle_rebinding_denied(package):
    root = cycle(package, 1)
    package.reserve(root)
    link = json.loads((root / "package-link.json").read_text())
    link["index"] = 2
    (root / "package-link.json").write_text(json.dumps(link))
    with pytest.raises(ValueError, match="identity"):
        package_for(root)
    data = json.loads(package.spec.read_text())
    data["deadline"] += 1
    package.spec.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="changed"):
        Package(package.root, package.hash)


def test_time_reserve_denies_before_allocating(package, monkeypatch):
    monkeypatch.setattr(
        "workbench.real_package.time.time", lambda: package.budget["deadline"] - 2999
    )
    with pytest.raises(ValueError, match="time ceiling"):
        package.reserve(cycle(package, 1))
    assert not package.reservations()


def test_lease_watchdog_shortens_to_global_restore_reserve(package, tmp_path):
    private = tmp_path / "private"
    private.mkdir()
    (private / "state.json").write_text(
        json.dumps(dict(deadline=package.budget["deadline"] + 1000))
    )
    lease = SimpleNamespace(private=private, metadata={})
    package.clamp_lease(lease)
    state = json.loads((private / "state.json").read_text())
    assert state["deadline"] == package.budget["deadline"]
    assert state["watchdog_deadline"] == state["deadline"] - 300
    assert lease.metadata["deadline"] == state["deadline"]


def test_unowned_scope_or_unreserved_root_denied(package, tmp_path):
    other = tmp_path / "unrelated"
    other.mkdir()
    with pytest.raises(ValueError, match="isolated"):
        package.reserve(other)
    with pytest.raises(ValueError, match="allocation"):
        package.verify(cycle(package, 1))


def test_aggregate_storage_keeps_original_package_and_real_evidence_caps(package, tmp_path):
    def sparse_parts(folder, size):
        # Exercise the aggregate ceilings without exceeding the unchanged16MiB
        # per-file safety limit of trusted verification commands.
        folder.mkdir()
        for index, start in enumerate(range(0, size, 8 << 20)):
            with (folder / str(index)).open("wb") as f:
                f.truncate(min(8 << 20, size - start))

    historical = tmp_path / "historical"
    sparse_parts(historical, 200 << 20)
    root = cycle(package, 1)
    package.reserve(root)
    assert package.enforce_storage()["real_run_bytes"] == 0
    real = root / "real-run"
    sparse_parts(real, (64 << 20) + 1)
    with pytest.raises(ValueError, match="storage"):
        package.enforce_storage()
    for part in real.iterdir():
        part.unlink()
    sparse_parts(package.root / "large", (128 << 20) + 1)
    with pytest.raises(ValueError, match="storage"):
        package.enforce_storage()
    assert sum(p.stat().st_size for p in historical.iterdir()) == 200 << 20
    assert all(p.stat().st_size <= 8 << 20 for p in tmp_path.rglob("*") if p.is_file())


def test_explicit_three_hour_one_cycle_profile_does_not_allow_second_attempt(package):
    data = json.loads(package.spec.read_text())
    data.update(
        deadline=data["started_at"] + 10800,
        max_cycles=1,
        max_probes=1,
        max_attempts=1,
        max_generated_tokens=24000,
        evidence_ceiling_bytes=72 << 20,
    )
    package.spec.write_text(json.dumps(data))
    bounded = Package(package.root)
    first = cycle(bounded, 1)
    bounded.reserve(first)
    finished(first)
    with pytest.raises(ValueError, match="exhausted"):
        bounded.reserve(cycle(bounded, 2))
    assert len(bounded.reservations()) == 1


def test_six_hour_completion_session_caps_three_new_cycles_and_capacity(package):
    data = json.loads(package.spec.read_text())
    data.update(
        deadline=data["started_at"] + 21600,
        max_cycles=3,
        max_probes=3,
        max_attempts=3,
        max_generated_tokens=100000,
    )
    package.spec.write_text(json.dumps(data))
    bounded = Package(package.root)
    for i in range(1, 4):
        root = cycle(bounded, i)
        bounded.reserve(root)
        finished(root)
    assert sum(x["generated_capacity"] for x in bounded.reservations()) == 72000 <= 100000
    with pytest.raises(ValueError, match="exhausted"):
        bounded.reserve(cycle(bounded, 4))


def test_package_reserves_only_unspent_evidence_without_raising_aggregate_cap(package):
    data = json.loads(package.spec.read_text())
    data["evidence_ceiling_bytes"] = 72 << 20
    package.spec.write_text(json.dumps(data))
    bounded = Package(package.root)
    before = bounded.remaining_evidence_reservation()
    (bounded.root / "owned-result").write_bytes(b"x" * 12345)
    assert bounded.remaining_evidence_reservation() == before - 12345
    assert (
        bounded.enforce_storage()["package_bytes"] + bounded.remaining_evidence_reservation()
        == 72 << 20
    )


def test_narrowed_single_slot_keeps_full_run_reserve_and_cannot_dispatch(package):
    data = dict(package.budget)
    data.update(deadline=data['started_at'] + 318.724755, max_cycles=1,
                max_probes=1, max_attempts=1, max_generated_tokens=24000)
    package.spec.write_text(json.dumps(data))
    bounded = Package(package.root)
    with pytest.raises(ValueError, match='time ceiling/reserve'):
        bounded.reserve(cycle(bounded, 1))
    assert bounded.reservations() == []
