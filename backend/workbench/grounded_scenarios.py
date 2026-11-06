"""Committed context and conservative mapping validation for the existing planner.

No provider, execution, approval or certification owner lives here. Unrecognized
Lua mappings hold for technical clarification rather than guessing semantics.
"""

import hashlib
import json
import re
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, model_validator
from workbench import regression_evidence as behavior, regression_preparation as preparation
from workbench.visual_scope import digest


Arguments = Annotated[list[behavior.Scalar], Field(max_length=8)]
SOURCE_PATH = TypeAdapter(behavior.LuaPath)


class ConcreteExample(behavior.Data):
    args: Arguments
    expected: behavior.Scalar
    incorrect: behavior.Scalar
    setup: Annotated[list[behavior.Setup], Field(max_length=8)] = []

    @model_validator(mode="after")
    def distinct_outcomes(self):
        json.dumps(self.model_dump(mode="json"), allow_nan=False)
        if self.expected == self.incorrect:
            raise ValueError("Expected and incorrect outcomes must differ.")
        return self


class ModuleObservation(behavior.Observation):
    path: Annotated[list[behavior.Key], Field(max_length=0)] = []


class ScalarReturnScenario(behavior.Scenario):
    args: Arguments
    source_paths: Annotated[list[behavior.LuaPath], Field(min_length=1, max_length=1)]
    setup: Annotated[list[behavior.Setup], Field(max_length=0)] = []
    observe: ModuleObservation


class ModuleScenario(ScalarReturnScenario):
    fixture: Literal["module"]


class ClientScenario(behavior.Scenario):
    fixture: Literal["client"]
    args: Arguments
    target: behavior.Keys


class LoadedClientScenario(ScalarReturnScenario):
    fixture: Literal["loaded-client"]
    target: behavior.Keys


class GroundedDraft(behavior.Data):
    # Expose existing compiler and mapping constraints to structured generation.
    # Reviewed/stored drafts keep the shared scenario representation.
    scenarios: Annotated[
        list[Annotated[ModuleScenario | ClientScenario | LoadedClientScenario, Field(discriminator="fixture")]],
        Field(min_length=1, max_length=3),
    ]
    unresolved: Annotated[list[behavior.Question], Field(max_length=0)] = []


class ClarificationDraft(behavior.Data):
    scenarios: Annotated[list[None], Field(max_length=0)] = []
    unresolved: Annotated[list[behavior.Question], Field(min_length=1, max_length=8)]


class Response(behavior.ScenarioResponse):
    draft: GroundedDraft | ClarificationDraft
    evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class BindingSelection(behavior.Data):
    id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{0,47}$")]
    binding_id: Annotated[int, Field(ge=1)]
    acceptance_index: Annotated[int, Field(ge=0, le=2)]
    args: Arguments
    expected: behavior.Scalar
    incorrect: behavior.Scalar


class LoadedClientDraft(behavior.Data):
    scenarios: Annotated[list[BindingSelection], Field(min_length=1, max_length=3)]


class NoBindingDraft(behavior.Data):
    unable_to_select: Literal[True]


class LoadedClientResponse(Response):
    # The provider selects an operation. Only Workbench can supply source fields.
    draft: LoadedClientDraft | NoBindingDraft


def planning_request(packet):
    bindings = packet["fixture_bindings"]
    if not any(binding["fixture"] == "loaded-client" for binding in bindings):
        return Response, packet
    options = []
    for binding in bindings:
        source, = binding["source_paths"]
        for op in binding["operations"]:
            if op["defined_in"] != source:
                raise ValueError("A loaded-client operation must come from its bound core addon source.")
            options.append(dict(binding_id=len(options) + 1, test_path=binding["test_path"],
                                fixture=binding["fixture"], source_path=source, **op))
    return LoadedClientResponse, {
        **{key: value for key, value in packet.items() if key != "fixture_bindings"},
        "binding_options": options,
        "supported_schema": "One existing test, up to three scalar first returns from verified addon callables.",
    }


def resolve_response(raw, schema, wire, packet):
    parsed = schema.model_validate(raw)
    if any(getattr(parsed, key) != packet[key] for key in
           ("input_sha256", "evidence_sha256", "policy_sha256")):
        raise ValueError("Generated scenario evidence/request/policy bindings are stale; prepare a fresh proposal.")
    if schema is LoadedClientResponse:
        if isinstance(parsed.draft, NoBindingDraft):
            raise ValueError("The model could not match the supplied example to a verified addon operation.")
        options = {option["binding_id"]: option for option in wire["binding_options"]}
        scenarios = []
        for selection in parsed.draft.scenarios:
            option = options.get(selection.binding_id)
            if option is None:
                raise ValueError("The proposal selected a callable outside the verified source bindings.")
            scenarios.append(LoadedClientScenario(
                id=selection.id, acceptance_index=selection.acceptance_index,
                test_path=option["test_path"], fixture=option["fixture"],
                source_paths=[option["source_path"]], target=option["target"], method=option["method"],
                args=selection.args, observe=ModuleObservation(expected=selection.expected),
                incorrect=selection.incorrect,
            ))
        if len({s.acceptance_index for s in scenarios}) != len(scenarios):
            raise ValueError("The proposal selected more than one callable for the same example.")
        parsed = Response(input_sha256=parsed.input_sha256, evidence_sha256=parsed.evidence_sha256,
                          policy_sha256=parsed.policy_sha256, model=parsed.model,
                          draft=GroundedDraft(scenarios=scenarios))
    validate_generation(parsed.draft, packet)
    return parsed


def context(original, feedback, contract, policy):
    root, base, _ = preparation.repository()
    if base != contract["resolved_base"] or contract["scope_missing"]:
        raise ValueError(
            "Resolve the selected committed test and repository evidence before scenario generation."
        )
    paths = contract["writable_paths"]
    selected = [(e["path"], e["git_blob"]) for e in contract["evidence"]]
    framework = {path: preparation.blob(root, oid) for path, oid in selected}
    harness = framework["tests/harness.lua"].decode()
    loaded = {}
    references = set()
    for path in paths:
        test = framework[path].decode()
        literal = loaders(test)
        if len(paths) == 1 and not literal and uses_client(test, harness):
            if source := client_chunk(test):
                loaded[path] = source
        references.update(literal)
        if path in loaded:
            references.add(loaded[path])
        elif uses_client(test, harness):
            references.update(loaders(harness))
    sources = committed_sources(root, base, references)
    selected += sources
    evidence = []
    total = 0
    for path, oid in selected:
        raw = framework[path] if path in framework else preparation.blob(root, oid)
        total += len(raw)
        if not loaded and (len(raw) > 12000 or total > 18000):
            raise ValueError(
                "Relevant committed source exceeds the scenario context bound; narrow the source/test selection."
            )
        evidence.append(
            dict(path=path, git_blob=oid, sha256=hashlib.sha256(raw).hexdigest(), text=raw.decode())
        )
    if loaded:
        path, source = next(iter(loaded.items()))
        module = next((e["text"] for e in evidence if e["path"] == source), "")
        exports = chunk_operations(source, module)
        bindings = [dict(test_path=path, fixture="loaded-client", source_paths=[source],
                         observation="scalar first return", operations=exports)] if exports else []
        # The planner needs the committed callable signatures and explicit examples.
        # Whole client harnesses and unrelated source bodies consume the wire budget.
        evidence = [{k: v for k, v in entry.items() if k != "text"} for entry in evidence]
    else:
        bindings = fixture_bindings(paths, [path for path, _ in sources], evidence)
    return dict(
        purpose="propose-regression-scenarios",
        original=original,
        feedback=feedback,
        repository="Twich-Team/TwichUI",
        resolved_base=base,
        writable_paths=paths,
        input_sha256=behavior.input_binding(original, feedback, base, paths),
        evidence=evidence,
        evidence_sha256=digest(dict(files=evidence, bindings=bindings) if loaded else evidence),
        fixture_bindings=bindings,
        examples=concrete_examples(original, feedback),
        policy_sha256=digest(policy),
        supported_schema="One existing test; up to three module/client scalar examples. Expected and meaningful incorrect outcomes are proposed intent, never certification.",
        preparation_policy=dict(
            category="lua-regression-tests",
            changes="additive-tests-only",
            execution_authorized=False,
            permissions=[],
            independent_review_required=True,
        ),
    )


def committed_sources(root, base, references):
    allowed = []
    for path in sorted(references):
        if ".." in path.split("/"):
            continue
        try:
            allowed.append(SOURCE_PATH.validate_python(path))
        except ValueError:
            continue
    if not allowed:
        return []
    if len(allowed) > 6:
        raise ValueError("The selected fixture references more than six Lua sources; narrow the test selection.")
    rows = preparation.git(root, "ls-tree", "-r", base, "--", *allowed).decode().splitlines()
    sources = []
    for row in rows:
        mode, kind, oid, path = row.split(maxsplit=3)
        if mode in ("100644", "100755") and kind == "blob" and path in allowed:
            sources.append((path, oid))
    return sources


def unique_json_object(pairs):
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError("Duplicate JSON field")
    return value


def concrete_examples(original, feedback):
    # Keep numbered examples from older requests and allow later clarification.
    examples = {}
    for is_clarification, text in ((False, original["description"]), (True, feedback)):
        if is_clarification and (example := original["regression_tests"].get("text_example")):
            examples[0] = ConcreteExample(
                args=[example["input"]], expected=example["expected"], incorrect=example["incorrect"]
            )
        for line in text.split("\n"):
            match = re.fullmatch(r"\s*Example ([1-8]):\s*(.*)", line)
            if match:
                index = int(match[1]) - 1
                try:
                    value = json.loads(match[2], object_pairs_hook=unique_json_object)
                    examples[index] = ConcreteExample.model_validate(value)
                except (ValueError, RecursionError):
                    examples[index] = None
    return [
        dict(acceptance_index=index, **example.model_dump(mode="json"))
        for index, example in sorted(examples.items())
        if example is not None and index < len(original["regression_tests"]["acceptance"])
    ]


class ScenarioInputRequired(ValueError):
    """A concrete question the requester can answer before planning."""


def require_plannable(packet):
    if len(packet["writable_paths"]) != 1:
        raise ScenarioInputRequired("Select one existing focused test for these behavioral examples.")
    if not packet["fixture_bindings"]:
        raise ValueError(
            f"For {packet['writable_paths'][0]}, choose an existing test with a supported "
            "module loader or harness client and a committed exported operation."
        )
    acceptance = packet["original"]["regression_tests"]["acceptance"]
    if len(acceptance) > 3:
        raise ScenarioInputRequired("Split this request into at most three expected results for one focused test.")
    supplied = {example["acceptance_index"]: example for example in packet["examples"]}
    for index, result in enumerate(acceptance):
        if index not in supplied:
            raise ScenarioInputRequired(
                f'For expected result {index + 1} ("{result[:250]}"), what exact arguments should the test '
                "pass, what should it observe, and which incorrect outcome should it reject? "
                f'Use one line: Example {index + 1}: {{"args": [<values>], "expected": <value>, '
                '"incorrect": <value>}. Use JSON values, preserve spaces inside strings, '
                "and use [] for a call with no arguments."
            )
        count = len(supplied[index]["args"])
        if supplied[index]["setup"] and any(b["fixture"] == "loaded-client" for b in packet["fixture_bindings"]):
            raise ValueError("The selected addon fixture observes a first return and cannot run the supplied setup.")
        if not any(accepts_argument_count(op, count) for binding in packet["fixture_bindings"]
                   for op in binding["operations"]):
            raise ScenarioInputRequired(
                f"For expected result {index + 1}, no committed callable matches the {count} explicit "
                f"arguments in Example {index + 1}. What exact arguments should the test pass? "
                "Include each parameter value or null for an explicit Lua nil."
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


def uses_client(test, harness):
    return re.search(r"(?<![\w.:])MakeClient\s*\(", code(test)) and re.search(
        r"\b(?:function\s+MakeClient|MakeClient\s*=\s*function)\s*\(", code(harness)
    )


IDENTIFIER = r"[A-Za-z_]\w*"
DIRECT_FUNCTION = re.compile(r"^\s*return\s+function\s*\(([^()]*)\)")


def client_chunk(text):
    """Recognize a literal ROOT load into the existing harness client."""
    literals = []

    def mask(token):
        if token[0].startswith("--"):
            return " "
        literals.append(token[0][1:-1] if token[0][0] in "\"'" else None)
        return f"@{len(literals) - 1}@"

    match = re.match(
        rf"\s*dofile\s*\(\s*TESTS\s*\.\.\s*@(\d+)@\s*\)\s*;?\s*"
        rf"local\s+(?P<client>{IDENTIFIER})\s*=\s*MakeClient\s*\(\s*@\d+@\s*,\s*\{{\s*@(\d+)@\s*\}}\s*\)\s*;?\s*"
        rf"local\s+(?P<chunk>{IDENTIFIER})\s*=\s*assert\s*\(\s*loadfile\s*\(\s*ROOT\s*\.\.\s*@(\d+)@\s*\)\s*\)\s*;?\s*"
        r"setfenv\s*\(\s*(?P=chunk)\s*,\s*(?P=client)\s*\)\s*;?\s*"
        r"(?P=chunk)\s*\(\s*@(\d+)@\s*,\s*\{\s*\}\s*\)",
        LITERALS.sub(mask, text),
    )
    if not match:
        return None
    harness, client, source, addon = (literals[int(match[i])] for i in (1, 3, 5, 6))
    if (harness == "harness.lua" and client == addon == "!!!TwichUI"
            and source and re.fullmatch(r"[A-Za-z0-9_/-]+\.lua", source)
            and not source.startswith("/")):
        return "!!!TwichUI/" + source


def chunk_operations(path, text):
    # This subset requires an explicit namespace published in the client's
    # globals. Function signatures are screened here and exercised in Lua.
    match = re.match(
        rf"\s*local\s+(?P<alias>{IDENTIFIER})\s*=\s*(?P<global_name>{IDENTIFIER})\s*"
        rf"local\s+(?P<table>{IDENTIFIER})\s*=\s*\{{\s*\}}\s*"
        rf"(?P=alias)\s*\.\s*(?P<namespace>{IDENTIFIER})\s*=\s*(?P=table)\b",
        code(text),
    )
    if not match:
        return []
    prefix = [match["global_name"], match["namespace"]]
    if any(key in behavior.FORBIDDEN or key.startswith("__") for key in prefix):
        return []
    return [
        dict(op, target=prefix + op["target"])
        for op in operations(path, text, match["table"])
        if len(prefix) + len(op["target"]) <= 8
    ]


def operation(path, target, params, method=False):
    parameters = [p.strip() for p in params.split(",")] if params.strip() else []
    variadic = bool(parameters and parameters[-1] == "...")
    if variadic:
        parameters.pop()
    if len(target) > 8 or any(k in behavior.FORBIDDEN or k.startswith("__") for k in target):
        return None
    if len(parameters) > 8 or any(not re.fullmatch(IDENTIFIER, p) for p in parameters):
        return None
    return dict(defined_in=path, target=target, method=method, parameters=parameters, variadic=variadic)


def operations(path, text, receiver, *, direct=False):
    symbols = code(text)
    if direct and (match := DIRECT_FUNCTION.match(symbols)):
        callable_ = operation(path, [], match[1])
        return [callable_] if callable_ else []
    if not receiver:
        return []
    target = rf"{re.escape(receiver)}((?:\s*[.:]\s*{IDENTIFIER})+)"
    declaration = rf"(?:\bfunction\s+{target}|(?<![\w.:]){target}\s*=\s*function)\s*\(([^()]*)\)"
    found = []
    for declared, assigned, params in re.findall(declaration, symbols):
        name = re.sub(r"\s+", "", declared or assigned)
        parts = re.split(r"[.:]", name[1:])
        method = ":" in name
        if method and (assigned or ":" in name[: -len(parts[-1]) - 1]):
            continue
        callable_ = operation(path, parts, params, method)
        if callable_:
            found.append(callable_)
    return found


def fixture_bindings(paths, sources, evidence):
    files = {e["path"]: e["text"] for e in evidence}
    harness_path = "tests/harness.lua"
    harness = files.get(harness_path, "")
    bindings = []
    for path in paths:
        test = files[path]
        module_loaders = loaders(test)
        for source in sorted(module_loaders.intersection(sources)):
            text = files[source]
            exports = operations(source, text, returned(text), direct=True)
            if exports:
                bindings.append(dict(
                    test_path=path, fixture="module", source_paths=[source],
                    observation="scalar first return", operations=exports,
                ))
        if uses_client(test, harness):
            client_sources = sorted((module_loaders | loaders(harness)).intersection(sources))
            receiver = returned(harness)
            exports = [
                op for source in [harness_path, *client_sources]
                for op in operations(source, files[source], receiver)
            ]
            if client_sources and exports:
                bindings.append(dict(
                    test_path=path, fixture="client", source_paths=client_sources,
                    observation="scalar first return or established client field", operations=exports,
                ))
    return bindings


def accepts_argument_count(signature, count):
    named = len(signature["parameters"])
    return count >= named if signature["variadic"] else count == named


def validate_call(example_id, binding, sources, target, method, args):
    matches = [
        op for op in binding["operations"]
        if op["target"] == target and op["method"] == method
        and (op["defined_in"] in sources or op["defined_in"] == "tests/harness.lua")
    ]
    if len(matches) != 1:
        raise ValueError(f"Example {example_id}: select one callable and calling convention from the committed fixture bindings.")
    signature = matches[0]
    if not accepts_argument_count(signature, len(args)):
        parameters = ", ".join(signature["parameters"]) or "none"
        raise ValueError(f"Example {example_id}: supply explicit arguments for the callable parameters ({parameters}).")


def validate_generation(draft, packet):
    # Retain the existing committed-fixture and unsafe path checks. The packet
    # further limits generation to surfaced signatures and explicit user data.
    validate_mapping(draft, packet)
    examples = {example["acceptance_index"]: example for example in packet["examples"]}
    if draft.unresolved:
        if any(q.acceptance_index not in examples or not q.question.strip() for q in draft.unresolved):
            raise ValueError("Ask a specific clarification about an existing expected result.")
        return
    if {s.acceptance_index for s in draft.scenarios} != examples.keys():
        raise ValueError("Cover the supplied expected results or return clarification only; result indices are zero-based.")
    for s in draft.scenarios:
        sources = set(s.source_paths)
        binding = next((
            b for b in packet["fixture_bindings"]
            if b["test_path"] == s.test_path and b["fixture"] == s.fixture
            and sources <= set(b["source_paths"])
        ), None)
        if binding is None:
            raise ValueError(f"Example {s.id}: select a binding established for the selected test.")
        validate_call(s.id, binding, sources, s.target, s.method, s.args)
        for step in s.setup:
            if step.op == "call":
                validate_call(s.id, binding, sources, step.path, step.method, step.args)
        actual = dict(acceptance_index=s.acceptance_index, args=s.args, expected=s.observe.expected,
                      incorrect=s.incorrect, setup=[step.model_dump(mode="json") for step in s.setup])
        if digest(actual) != digest(examples[s.acceptance_index]):
            raise ValueError(f"Example {s.id}: arguments, setup and outcomes must match the user's explicit example.")


def validate_mapping(draft, packet):
    files = {e["path"]: e.get("text", "") for e in packet["evidence"]}
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
        if s.fixture == "loaded-client":
            if s.addons != ["!!!TwichUI"]:
                raise ValueError(f"Example {s.id}: use the addon loaded by the committed client test.")
            # validate_generation requires an exact committed callable binding.
        elif s.fixture == "module":
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
