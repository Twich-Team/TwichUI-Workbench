"""Bounded Lua recipe data, registered by trusted preparation, never model input."""

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re


def validate(spec):
    from workbench.task_budget import valid_contract

    if not isinstance(spec, dict) or not valid_contract(spec.get("contract", {})):
        raise ValueError("Unsupported bounded Lua contract")
    if spec.get('read_budget_policy', 'repeated-v1') not in ('repeated-v1', 'distinct-lines-v1'):
        raise ValueError('Unknown bounded source read policy')
    paths = spec.get("read_paths", spec["contract"]["paths"])
    if (
        not isinstance(paths, list)
        or not 1 <= len(paths) <= 8
        or not set(spec["contract"]["paths"]).issubset(paths)
        or any(
            not isinstance(p, str)
            or not re.fullmatch(r"[A-Za-z0-9_!./-]{1,240}", p)
            or PurePosixPath(p).is_absolute()
            or ".." in PurePosixPath(p).parts
            or not p.endswith(".lua")
            or not p.startswith(("!!!TwichUI/", "TwichUI_Settings/", "tests/test_"))
            for p in paths
        )
        or spec.get("focused_test") not in paths
        or not spec["focused_test"].startswith("tests/test_")
        or not re.fullmatch("[0-9a-f]{64}", spec.get("oracle_sha256", ""))
        or not isinstance(spec.get("instruction"), str)
        or not 1 <= len(spec["instruction"]) <= 12000
        or spec.get("base_branch") != "main"
        or spec.get("draft") is not True
    ):
        raise ValueError("Invalid fixed recipe paths, oracle or instruction")
    reads = spec.get("initial_reads")
    if (
        not isinstance(reads, list)
        or len(reads) != 4
        or any(
            not isinstance(r, dict)
            or r.get("path") not in paths
            or type(r.get("start")) is not int
            or type(r.get("end")) is not int
            or not 1 <= r["start"] <= r["end"] <= r["start"] + 249
            for r in reads
        )
    ):
        raise ValueError("Exactly four bounded initial windows required")
    identity = spec.get("identity")
    if (
        not isinstance(identity, dict)
        or set(identity) != {"name", "email"}
        or any(
            not isinstance(v, str) or not 1 <= len(v) <= 160 or any(ord(c) < 32 for c in v)
            for v in identity.values()
        )
    ):
        raise ValueError("Bounded repository-local publication identity required")
    if spec.get("category", "Bug fix") not in (
        "Bug fix",
        "Compatibility",
        "Reliability",
        "Performance",
        "Usability / visual",
        "Feature",
        "Tests",
        "Maintenance",
    ):
        raise ValueError("Unknown task category")
    controls = spec.get("controls")
    if 'verification_profile' in spec:
        from workbench.verification_profiles import validate_recipe
        validate_recipe(spec)
    elif (
        not isinstance(controls, dict)
        or controls.get("original") != "fail"
        or controls.get("known-good-oracle-control") != "pass"
        or not 3 <= len(controls) <= 8
        or any(not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", key) for key in controls)
        or not any(
            value == "fail"
            for key, value in controls.items()
            if key not in ("original", "known-good-oracle-control")
        )
        or any(v not in ("pass", "fail") for v in controls.values())
    ):
        raise ValueError("Reproduction, positive and negative controls required")
    if len(json.dumps(spec, allow_nan=False).encode()) > 65536:
        raise ValueError("Recipe size limit")
    return spec


def load(root):
    path = Path(root) / "recipe.json"
    if not path.exists():
        return None
    if path.is_symlink() or path.stat().st_uid != os.getuid() or path.stat().st_size > 65536:
        raise ValueError("Owned bounded fixed recipe required")
    spec = validate(json.loads(path.read_text()))
    oracle = Path(root) / "recipe-oracle.lua"
    if (
        oracle.is_symlink()
        or not oracle.is_file()
        or oracle.stat().st_uid != os.getuid()
        or oracle.stat().st_size > 65536
        or hashlib.sha256(oracle.read_bytes()).hexdigest() != spec["oracle_sha256"]
    ):
        raise ValueError("Independent recipe oracle identity mismatch")
    return spec


def from_review(connection, ref, *, oracle, behavior_id, read_paths, focused_test, identity):
    """Compile task data from the actual applied review, never a task registry.

    Oracle semantics and bounded read selection still require trusted preparation.
    This function supplies neither certification nor execution permission.
    """
    from workbench import real_execution, task_execution, regression_preparation
    from workbench.verification_profiles import select, requirements
    from workbench.real_workspace import PRELUDE

    linked = task_execution.snapshot(connection, **ref)
    reviewed = linked['proposal_review'].get('task_contract')
    if not reviewed or linked['dependencies']:
        raise ValueError('A current reviewed Lua contract without prerequisites is required')
    profile = select(reviewed['category'])
    policy = ('additions-only-existing-test-files' if reviewed['category'] == 'lua-regression-tests'
              else 'bounded-existing-lua-files')
    if (reviewed['scope_missing'] or reviewed['verification_profile'] != profile
            or reviewed['change_policy'] != policy or not reviewed['acceptance']
            or any(not a.strip() for a in reviewed['acceptance'])
            or not reviewed['intended_behavior'].strip()):
        raise ValueError('Incomplete or changed reviewed verification requirements')
    root, base, _ = regression_preparation.repository()
    if (base != reviewed['resolved_base']
            or regression_preparation.git(root, 'rev-parse', '--verify', 'refs/heads/main').decode().strip() != base
            or regression_preparation.git(root, 'status', '--porcelain=v1', '--ignored')):
        raise ValueError('Current clean repository/main base differs from the reviewed contract')
    limits = {**real_execution.LIMITS, 'overall_seconds': 5400, 'implementation_attempts': 1,
              'publication_seconds': 900, 'fallback_attempts': 0}
    if (reviewed['resource_limits'] != limits or reviewed['pr_repository'] != reviewed['repository']
            or reviewed['pr_base_branch'] != 'main' or reviewed['pr_draft'] is not True):
        raise ValueError('Reviewed resource limits or PR destination are unsupported')
    if not isinstance(oracle, str) or not 1 <= len(oracle.encode()) <= 65536:
        raise ValueError('Bounded independent task oracle required')
    prepared = reviewed.get('behavioral_evidence')
    if prepared and prepared['status'] == 'review_required':
        from workbench.regression_evidence import compile_plan, validate_expectation_review
        validate_expectation_review(linked['proposal_review'], linked['approval']['payload'])
        compiled = compile_plan(prepared)
        if (oracle != compiled['oracle'] or behavior_id != compiled['behavior_id']
                or read_paths != compiled['read_paths'] or focused_test != compiled['focused_test']):
            raise ValueError('Scenario oracle/read selection differs from the independently reviewed examples')
    contract = real_execution.contract()
    contract.update(task_id=linked['task']['id'], title=linked['task']['title'],
                    repository=reviewed['repository'], source=str(root), base=base,
                    paths=reviewed['writable_paths'])
    spec = dict(contract=contract, read_paths=read_paths, focused_test=focused_test,
                oracle_sha256=hashlib.sha256(oracle.encode()).hexdigest(),
                verification_profile=profile['id'],
                behavior=dict(id=behavior_id, review_binding=linked['proposal_review']['binding']),
                controls=requirements(profile['id'])['controls'],
                instruction=json.dumps(dict(request=linked['original'], behavior=reviewed['intended_behavior'],
                    acceptance=reviewed['acceptance'], feedback=reviewed['review_feedback'],
                    scope=linked['approved_scope'], exclusions=linked['proposal_review']['content']['exclusions']),
                    ensure_ascii=True),
                initial_reads=[dict(path=read_paths[i % len(read_paths)], start=1, end=120) for i in range(4)] if read_paths else [],
                base_branch=reviewed['pr_base_branch'], draft=reviewed['pr_draft'], identity=identity,
                category='Tests' if reviewed['category'] == 'lua-regression-tests' else 'Bug fix')
    validate(spec)
    # Every command advertised to the reviewer must be consumed by this runtime.
    checks = [dict(argv=['luac5.1', '-p', p], cwd='.') for p in contract['paths']]
    checks += [dict(argv=['lua5.1', '-e', PRELUDE, focused_test.split('/')[-1]], cwd='tests'),
               dict(argv=['bash', 'tests/run.sh'], cwd='.')]
    if [{k: c[k] for k in ('argv', 'cwd')} for c in reviewed['verification_commands']] != checks:
        raise ValueError('Reviewed checks differ from the supported fixed runtime checks')
    # Pin actual committed regular bytes, including the harness and selected scope.
    evidence = {e['path']: e for e in reviewed['evidence']}
    if (not set(['tests/run.sh', 'tests/harness.lua', *read_paths]).issubset(evidence)
            or len(evidence) != len(reviewed['evidence']) or len(evidence) > 12):
        raise ValueError('Reviewed file/framework evidence is incomplete')
    for path in evidence:
        entry = regression_preparation.git(root, 'ls-tree', base, '--', path).decode().strip().split()
        if len(entry) != 4 or entry[0] not in ('100644', '100755') or entry[1] != 'blob' or entry[3] != path:
            raise ValueError('Reviewed file must be an existing regular committed file')
        raw = regression_preparation.blob(root, entry[2])
        if evidence[path] != dict(path=path, git_blob=entry[2], sha256=hashlib.sha256(raw).hexdigest()):
            raise ValueError('Reviewed repository evidence differs')
    return spec
