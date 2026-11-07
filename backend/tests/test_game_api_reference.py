"""Pinned generated API context is bounded untrusted source, never executable code."""

import hashlib
import json

import pytest
from workbench import game_api_reference as api


def setup_cache(tmp_path, monkeypatch, docs):
    monkeypatch.setattr(api, "ROOT", tmp_path)
    cache = tmp_path / ".local/game-api-reference"
    cache.mkdir(parents=True)
    entries = []
    for name, namespace, text in docs:
        path = cache / name
        path.write_text(text)
        raw = path.read_bytes()
        entries.append(
            dict(
                file=name,
                namespace=namespace,
                bytes=len(raw),
                sha256=hashlib.sha256(raw).hexdigest(),
            )
        )
    manifest = dict(
        directory="generated",
        commit="a" * 40,
        origin="https://example.invalid/public-api",
        client="fixture",
        entries=entries,
    )
    (tmp_path / "reference_data").mkdir()
    (tmp_path / "reference_data/forever-api.json").write_text(json.dumps(manifest))
    return cache


def files(text):
    return [dict(excerpts=[dict(text=text)])]


def test_only_actual_namespace_calls_select_declarations_not_strings_or_comments(
    tmp_path, monkeypatch
):
    setup_cache(
        tmp_path, monkeypatch, [("ExampleDocumentation.lua", "C_Example", "SOURCE DATA ONLY")]
    )
    assert api.collect(files('-- C_Example.Do()\nlocal s="C_Example.Do()"')) == []
    result = api.collect(files("C_Example.Do(42)"))
    assert result[0]["requested_functions"] == ["Do"] and result[0]["source_revision"] == "a" * 40
    assert result[0]["excerpts"][0]["text"] == "SOURCE DATA ONLY"
    assert "no live-game observation" in result[0]["kind"]
    assert api.collect(files("C_Missing.Do()")) == []
    alias = api.collect(files("local operation = C_Example.Do; operation(42)"))
    assert alias[0]["requested_functions"] == ["Do"]


def test_shared_namespace_documents_are_not_silently_overwritten(tmp_path, monkeypatch):
    setup_cache(
        tmp_path,
        monkeypatch,
        [(f"{n}Documentation.lua", "C_Example", n) for n in ("First", "Second")],
    )
    result = api.collect(files("C_Example.Do()"))
    assert [r["excerpts"][0]["text"] for r in result] == ["First", "Second"]


@pytest.mark.parametrize("fault", ["changed", "link", "directory", "missing", "cache-link"])
def test_modified_linked_nonregular_or_missing_cache_holds(tmp_path, monkeypatch, fault):
    cache = setup_cache(tmp_path, monkeypatch, [("ExampleDocumentation.lua", "C_Example", "abc")])
    path = cache / "ExampleDocumentation.lua"
    if fault == "changed":
        path.write_text("xyz")
    elif fault == "link":
        path.unlink()
        path.symlink_to(tmp_path / "outside")
    elif fault == "directory":
        path.unlink()
        path.mkdir()
    elif fault == "missing":
        path.unlink()
    else:
        cache.rename(cache.with_name("moved"))
        cache.symlink_to(cache.with_name("moved"))
    with pytest.raises((ValueError, OSError)):
        api.collect(files("C_Example.Do()"))


def test_large_file_selects_exact_function_related_types_and_events_with_hard_text_bound():
    def block(name, kind, extra=""):
        return (
            '\t\t{\n\t\t\tName = "'
            + name
            + '",\n\t\t\tType = "'
            + kind
            + '",\n'
            + extra
            + "\t\t},\n"
        )

    text = "-- public generated data\n" + block(
        "Do", "Function", '\t\t\tReturns = { { Type = "Result" } },\n'
    )
    text += (
        block("Result", "Structure")
        + block("Changed", "Event")
        + block("Unrelated", "Function")
        + "-- filler\n" * 200
    )
    parts = api.excerpt(text, {"Do"}, 800)
    joined = "\n".join(p["text"] for p in parts)
    assert '"Do"' in joined and '"Result"' in joined and '"Changed"' in joined
    assert '"Unrelated"' not in joined and sum(len(p["text"]) for p in parts) <= 800
    for part in parts:
        assert part["text"] == "\n".join(text.splitlines()[part["line"] - 1 : part["through_line"]])
    assert api.excerpt(text, {"Do"}, 10) == []


@pytest.mark.skipif(
    not (api.ROOT / ".local/game-api-reference").is_dir(),
    reason="Explicitly staged pinned Forever API reference",
)
def test_installed_target_manifest_and_cache_provide_forever_declarations_without_network():
    result = api.collect(files("C_TaxiMap.GetTaxiNodesForMap(1)"))
    assert result and result[0]["client"] == "WoW Forever"
    assert result[0]["source_revision"] == "eac30af597e5aaa43a60f6675ef5fdcea100375f"
    text = "\n".join(p["text"] for p in result[0]["excerpts"])
    assert "GetTaxiNodesForMap" in text and "MapTaxiNodeInfo" in text
