"""Fixed application verification through the existing command owner; no inference."""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time

from workbench import real_confine
from workbench.verification_process import VerificationProcess

DIRECTORIES = (
    ".github",
    "backend",
    "docs",
    "e2e",
    "fixtures",
    "frontend",
    "migrations",
    "milestones",
    "scripts",
    "tools",
)
FILES = (
    "AGENTS.md",
    "CLAUDE.md",
    "README.md",
    "alembic.ini",
    "package.json",
    "package-lock.json",
    "playwright.config.ts",
    "pyproject.toml",
    "requirements.in",
    "requirements.lock",
)


def source_manifest(root):
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for folder in DIRECTORIES
        for p in (root / folder).rglob("*")
        if p.is_file()
        and not any(x in p.parts for x in ("__pycache__", "node_modules", "dist", ".pytest_cache"))
    } | {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in FILES}


@contextmanager
def isolated_copy(source):
    source = Path(source).resolve()
    before = source_manifest(source)
    with tempfile.TemporaryDirectory(prefix="workbench-verify-") as temporary:
        root = Path(temporary) / "source"
        root.mkdir()
        for name in DIRECTORIES:
            shutil.copytree(
                source / name,
                root / name,
                ignore=shutil.ignore_patterns(
                    "__pycache__", "node_modules", "dist", ".pytest_cache"
                ),
            )
        for name in FILES:
            shutil.copy2(source / name, root / name)
        assert source_manifest(root) == before, "Source changed while freezing verification copy"
        (root / ".venv").symlink_to(source / ".venv", target_is_directory=True)
        (root / "node_modules").mkdir()
        for dependency in (source / "node_modules").iterdir():
            if dependency.name not in {".vite", ".vite-temp"}:
                (root / "node_modules" / dependency.name).symlink_to(
                    dependency, target_is_directory=dependency.is_dir()
                )
        (root / ".local").mkdir()
        (root / ".home").mkdir()
        # Only the disposable configuration changes. Reference PNGs are untouched.
        config = root / "playwright.config.ts"
        config.write_text(
            config.read_text()
            .replace("video: 'retain-on-failure'", "video: 'off'")
            .replace(
                "reducedMotion: 'reduce'",
                "reducedMotion: 'reduce', launchOptions:{args:"
                "['--no-zygote','--renderer-process-limit=1','--num-raster-threads=1','--disable-gpu']}",
            )
        )
        yield root


def configuration(root, source):
    source, root = Path(source).resolve(), Path(root).resolve()
    # Read tool discovery code, never the ambient credential-bearing environment.
    import runpy

    discovered = runpy.run_path(str(source / "scripts/project_env.py"))["environment"]()
    env = {
        k: discovered[k]
        for k in ("PATH", "PLAYWRIGHT_BROWSERS_PATH", "LD_LIBRARY_PATH")
        if k in discovered
    }
    node = Path(shutil.which("node", path=env["PATH"])).resolve()
    env.update(
        PATH=str(node.parent) + os.pathsep + env["PATH"],
        HOME=str(root / ".home"),
        LANG="C.UTF-8",
        PYTHONPATH=str(root / "backend"),
        OPENSSL_CONF="/dev/null",
        CI="1",
        NODE_OPTIONS="--v8-pool-size=1",
        UV_THREADPOOL_SIZE="1",
        GOMAXPROCS="2",
        RAYON_NUM_THREADS="2",
        GIT_CONFIG_GLOBAL="/dev/null",
        GIT_CONFIG_NOSYSTEM="1",
        NO_PROXY="127.0.0.1,localhost",
        no_proxy="127.0.0.1,localhost",
    )
    ro = [
        str(node.parent),
        str(source / "node_modules"),
        str(source / ".venv"),
        "/tmp/twichui-workbench-tools",
        "/tmp/twichui-dashboard-browsers",
        "/tmp/twichui-dashboard-libs",
        "/proc",
        "/etc/passwd",
        "/etc/group",
        "/etc/nsswitch.conf",
        "/etc/fonts",
        "/sys/devices/system/cpu",
        "/sys/bus/pci/devices",
    ]
    ro += [str(p.resolve()) for p in Path("/sys/bus/pci/devices").iterdir()]
    # Trusted legacy regressions read their original immutable checkout. Model
    # tools do not receive this read rule or any verification capability.
    ro.append("/home/rich/twichui-pilot/checkouts/base-f48b2e0bb")
    python = str(root / ".venv/bin/python")
    commands = {
        "check": [python, "scripts/wb.py", "check"],
        "backend": [python, "-m", "pytest", "-q"],
        "frontend": [
            str(node),
            str(root / "node_modules/vitest/vitest.mjs"),
            "run",
            "--config",
            str(root / "frontend/vite.config.ts"),
        ],
        "browser": [
            str(node),
            str(root / "node_modules/@playwright/test/cli.js"),
            "test",
            "--config",
            str(root / "playwright.config.ts"),
            "--project=chromium",
        ],
        "visual": [
            str(node),
            str(root / "node_modules/@playwright/test/cli.js"),
            "test",
            "--config",
            str(root / "playwright.config.ts"),
            "--project=visual",
        ],
    }
    return env, ro, commands


def verify(
    root,
    source,
    evidence,
    names=("check", "backend", "frontend", "browser", "visual"),
    aggregate_s=900,
):
    """Fixed suite commands, private loopback,128MiB trusted files/128tasks only."""
    root, evidence = Path(root).resolve(), Path(evidence).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    env, ro, commands = configuration(root, source)
    real_confine.USAGE_LOG = str(evidence / "resource-usage.jsonl")
    started = time.monotonic()
    results = []
    for name in names:
        if name not in commands:
            raise ValueError("Unknown fixed verification check")
        remaining = aggregate_s - (time.monotonic() - started)
        if remaining <= 0:
            raise ValueError("Aggregate verification time exhausted")
        command = commands[name]
        result = real_confine.run(
            command,
            str(root),
            min(120 if name in ("check", "frontend") else 600, remaining),
            env=env,
            ro=ro,
            rw=[str(root)],
            private_loopback=True,
            verification=VerificationProcess(tuple(command), str(root), name == "backend"),
            label="application-" + name,
        )
        result.update(
            name=name,
            command=command,
            network_mode="private-loopback",
            role="trusted verification; not model evidence",
        )
        (evidence / (name + ".json")).write_text(json.dumps(result, indent=2) + "\n")
        results.append(result)
        if result["status"] != "pass" or result["rc"] != 0:
            break
    return results
