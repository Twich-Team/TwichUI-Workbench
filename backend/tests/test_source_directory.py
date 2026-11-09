"""File navigation is bounded metadata, never source disclosure or permission."""

import copy
import json
from types import SimpleNamespace

import pytest

from workbench import source_directory as navigation
from workbench import regression_preparation as source, bug_investigation as bug


def entry(path, *, mode="100644", size=200):
    return f"{mode} blob {'a' * 40} {size}\t{path}\0".encode()


@pytest.fixture
def inventory(monkeypatch, tmp_path):
    rows = [
        entry("!!!TwichUI/npcs/Read.lua"),
        entry("TwichUI_Settings/pages/Knowledge.lua"),
        entry("tests/test_knowledge.lua"),
        entry("!!!TwichUI/libs/third_party.lua"),
        entry("!!!TwichUI/.private.lua"),
        entry("!!!TwichUI/link.lua", mode="120000"),
        entry("!!!TwichUI/oversized.lua", size=131073),
    ]
    calls = []
    monkeypatch.setattr(source, "repository", lambda: (tmp_path, "pinned", None))
    monkeypatch.setattr(bug, "clean_base", lambda root, base: calls.append(("clean", base)))

    def git(*args, **kwargs):
        calls.append(("inventory", args, kwargs))
        return b"".join(rows)

    monkeypatch.setattr(source, "git", git)

    def no_bodies(*args, **kwargs):
        raise AssertionError("Metadata navigation read a source body")

    monkeypatch.setattr(source, "blob", no_bodies)
    return SimpleNamespace(rows=rows, calls=calls, root=tmp_path)


def test_source_and_settings_and_tests_are_discoverable_without_being_selected(inventory):
    result = navigation.directory("pinned")
    assert result["complete"] and result["files_count"] == 3
    assert result["directories"] == {
        "!!!TwichUI/npcs": ["Read.lua"],
        "TwichUI_Settings/pages": ["Knowledge.lua"],
        "tests": ["test_knowledge.lua"],
    }
    assert "not inspected code or repair permission" in result["note"]
    assert "third_party" not in json.dumps(result) and ".private" not in json.dumps(result)


def test_external_disclosure_directory_contains_only_existing_permitted_paths(inventory):
    allowed = {"!!!TwichUI/npcs/Read.lua"}
    before = copy.deepcopy(allowed)
    result = navigation.directory("pinned", allowed)
    assert result["files_count"] == 1 and result["directories"] == {"!!!TwichUI/npcs": ["Read.lua"]}
    assert (
        allowed == before and "Only the existing externally permitted" in result["disclosure_scope"]
    )


def test_empty_external_permission_never_enumerates_the_project(inventory):
    result = navigation.directory("pinned", set())
    assert result["complete"] and not result["directories"] and not result["files_count"]
    assert not any(call[0] == "inventory" for call in inventory.calls)


def test_repository_drift_is_a_hold(inventory, monkeypatch):
    monkeypatch.setattr(source, "repository", lambda: (inventory.root, "changed", None))
    with pytest.raises(ValueError, match="project changed"):
        navigation.directory("pinned")


def test_large_navigation_never_silently_claims_a_complete_directory(inventory):
    inventory.rows[:] = [
        entry(f"!!!TwichUI/LongDirectoryName/LongModuleName{i:05}.lua") for i in range(2000)
    ]
    result = navigation.directory("pinned")
    assert (
        result["complete"] is False
        and result["files_count"] == 2000
        and result["directories"] == {}
    )
    assert "no absence may be inferred" in result["note"]
