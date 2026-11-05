"""Committed context and conservative mapping validation for the existing planner.

No provider, execution, approval or certification owner lives here. Unrecognized
Lua mappings hold for technical clarification rather than guessing semantics.
"""

import hashlib
import json
import re
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter
from workbench import regression_evidence as behavior, regression_preparation as preparation
from workbench.visual_scope import digest


class ModuleObservation(behavior.Observation):
    path: Annotated[list[behavior.Key], Field(max_length=0)] = []


class ModuleScenario(behavior.Scenario):
    fixture: Literal["module"]
    source_paths: Annotated[list[behavior.LuaPath], Field(min_length=1, max_length=1)]
    setup: Annotated[list[behavior.Setup], Field(max_length=0)] = []
    observe: ModuleObservation


class ClientScenario(behavior.Scenario):
    fixture: Literal["client"]
    target: behavior.Keys


class GroundedDraft(behavior.ScenarioDraft):
    # Expose existing compiler and mapping constraints to structured generation.
    # Reviewed/stored drafts keep the shared scenario representation.
    scenarios: Annotated[
        list[Annotated[ModuleScenario | ClientScenario, Field(discriminator="fixture")]],
        Field(max_length=3),
    ] = []


class Response(behavior.ScenarioResponse):
    draft: GroundedDraft
    evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


def context(original, feedback, contract, policy):
    root, base, _ = preparation.repository()
    if base != contract["resolved_base"] or contract["scope_missing"]:
        raise ValueError(
            "Resolve the selected committed test and repository evidence before scenario generation."
        )
    paths = contract["writable_paths"]
    # A bounded inventory lets the planner select supplied evidence, never host paths.
    inventory = []
    for line in (
        preparation.git(
            root, "ls-tree", "-r", base, "--", "!!!TwichUI", "TwichUI_Settings", max_bytes=262144
        )
        .decode()
        .splitlines()
    ):
        mode, kind, oid, path = line.split(maxsplit=3)
        if mode in ("100644", "100755") and kind == "blob":
            try:
                TypeAdapter(behavior.LuaPath).validate_python(path)
                inventory.append((path, oid))
            except ValueError:
                pass
    words = set(re.findall(r"[a-z]{3,}", json.dumps(original).lower()))
    inventory.sort(key=lambda p: (-sum(w in p[0].lower() for w in words), p[0]))
    selected = [(e["path"], e["git_blob"]) for e in contract["evidence"]]
    references = set()
    for path, oid in selected:
        if path.endswith(".lua"):
            references.update(loaders(preparation.blob(root, oid).decode()))
    inventory.sort(
        key=lambda p: (p[0] not in references, -sum(w in p[0].lower() for w in words), p[0])
    )
    selected += inventory[:6]
    evidence = []
    for path, oid in dict(selected).items():
        raw = preparation.blob(root, oid)
        if len(raw) > 12000 or sum(len(e["text"].encode()) for e in evidence) + len(raw) > 18000:
            raise ValueError(
                "Relevant committed source exceeds the scenario context bound; narrow the source/test selection."
            )
        evidence.append(
            dict(path=path, git_blob=oid, sha256=hashlib.sha256(raw).hexdigest(), text=raw.decode())
        )
    return dict(
        purpose="propose-regression-scenarios",
        original=original,
        feedback=feedback,
        repository="Twich-Team/TwichUI",
        resolved_base=base,
        writable_paths=paths,
        input_sha256=behavior.input_binding(original, feedback, base, paths),
        evidence=evidence,
        evidence_sha256=digest(evidence),
        policy_sha256=digest(policy),
        supported_schema="One existing test; up to three module/client scalar examples. Expected and meaningful incorrect outcomes are proposed intent, never certification.",
        preparation_policy=dict(
            category="lua-regression-tests",
            changes="additive-tests-only",
            execution_authorized=False,
            permissions=[],
            independent_review_required=True,
        ),
        instruction="Use only supplied committed evidence and exported operations. Do not infer intent from current output. Return specific unresolved questions for ambiguity or unsupported mappings. Echo the three bindings exactly. No Lua, permissions, checks or certification fields.",
    )


LITERALS = re.compile(
    r'--\[(=*)\[.*?\]\1\]|--[^\n]*|\[(=*)\[.*?\]\2\]|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', re.S
)


def code(text):
    # A lexical screen, never a Lua semantic proof. Literal loaders and explicit
    # returned receivers are the supported automatic mapping subset.
    return LITERALS.sub(" ", text)


def loaders(text):
    tokens = {m.start(): m for m in LITERALS.finditer(text)}
    masked = LITERALS.sub(lambda m: " " * len(m[0]), text)
    paths = set()
    for call in re.finditer(r"\bdofile\s*\(", masked):
        start = call.end()
        while start < len(text) and text[start].isspace():
            start += 1
        token = tokens.get(start)
        if not token or token[0][:1] not in {'"', "'"}:
            continue
        value = token[0][1:-1]
        if (
            value.startswith("../")
            and re.match(r"\s*\)", text[token.end() :])
            and re.fullmatch(r"[A-Za-z0-9_./!-]+", value)
        ):
            paths.add(value[3:])
    return paths


def returned(text):
    match = re.search(r"\breturn\s+([A-Za-z_]\w*)\s*(?:end\s*)?$", code(text))
    return match[1] if match else None


def validate_mapping(draft, packet):
    files = {e["path"]: e["text"] for e in packet["evidence"]}
    for s in draft.scenarios:
        if s.test_path not in packet["writable_paths"] or any(
            p not in files for p in s.source_paths
        ):
            raise ValueError(
                f"Example {s.id}: source/test selection is absent from supplied committed evidence."
            )
        test = files[s.test_path]
        source = "\n".join(files[p] for p in s.source_paths)
        symbols = code(source)
        if s.fixture == "module":
            if len(s.source_paths) != 1 or s.source_paths[0] not in loaders(test):
                raise ValueError(
                    f"Example {s.id}: the selected test does not map to this module source; clarify the fixture mapping."
                )
            if not s.target:
                if not re.search(r"^\s*return\s+function\s*\(", symbols):
                    raise ValueError(
                        f"Example {s.id}: a directly returned module function is not established by the committed source."
                    )
            elif not exported(symbols, s.target, returned(source)):
                raise ValueError(
                    f"Example {s.id}: exported operation {'.'.join(s.target)} is not established by committed source."
                )
        else:
            harness = files.get("tests/harness.lua", "")
            if "MakeClient" not in code(test) or "MakeClient" not in code(harness):
                raise ValueError(
                    f"Example {s.id}: the committed test/harness does not establish a client fixture."
                )
            if any(p not in loaders(test + "\n" + harness) for p in s.source_paths) or set(
                s.addons
            ) != {p.split("/")[0] for p in s.source_paths}:
                raise ValueError(
                    f"Example {s.id}: client addon/source mapping is unsupported; identify the committed fixture loader."
                )
            if not exported(code(source + "\n" + harness), s.target, returned(harness)):
                raise ValueError(
                    f"Example {s.id}: exported client operation {'.'.join(s.target)} is not established by committed evidence."
                )
            for step in s.setup:
                if step.op == "call" and not exported(
                    code(source + "\n" + harness), step.path, returned(harness)
                ):
                    raise ValueError(
                        f"Example {s.id}: setup operation {'.'.join(step.path)} is not established by committed evidence."
                    )
            receiver = returned(harness)
            for path in [s.observe.path, *(step.path for step in s.setup if step.op == "set")]:
                symbol = (
                    r"\s*\.\s*".join(re.escape(k) for k in [receiver, *path]) if receiver else ""
                )
                if path and (
                    not symbol
                    or not re.search(r"\b" + symbol + r"\b", code(source + "\n" + harness))
                ):
                    raise ValueError(
                        f"Example {s.id}: fixture field {'.'.join(path)} is not established by committed evidence; clarify its setup and source mapping."
                    )


def exported(text, path, receiver):
    if not receiver:
        return False
    symbol = re.escape(receiver) + r"\s*[.:]\s*" + r"\s*[.:]\s*".join(re.escape(k) for k in path)
    return bool(
        re.search(
            r"\b(?:function\s+" + symbol + r"\s*\(|" + symbol + r"\s*=\s*function\s*\()", text
        )
    )
