"""Freeze/copy generated API declarations from the existing readonly Forever checkout."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

PREFIX = "Interface/AddOns/Blizzard_APIDocumentationGenerated"
ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "reference_data/forever-api.json"


def inventory(source):
    if source.is_symlink() or source.resolve() != source:
        raise ValueError("Use the actual existing reference checkout")

    def git(*args):
        return subprocess.check_output(
            ["git", "--no-optional-locks", "-C", str(source), *args], text=True, timeout=15
        ).strip()

    if git("branch", "--show-current") != "forever" or git(
        "status", "--porcelain", "--untracked-files=no"
    ):
        raise ValueError("The readonly Forever reference must be on its clean existing branch")
    commit = git("rev-parse", "HEAD")
    tracked = set(git("ls-tree", "-r", "--name-only", commit, "--", PREFIX).splitlines())
    entries = []
    for path in sorted((source / PREFIX).glob("*Documentation.lua")):
        if str(path.relative_to(source)) not in tracked:
            raise ValueError("Do not include uncommitted reference declarations")
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 524288:
            raise ValueError("Reference declaration path or size invalid")
        raw = path.read_bytes()
        if not raw or raw.startswith(b"\x1b"):
            raise ValueError("Reference must be bounded Lua source data, never bytecode")
        text = raw.decode("utf-8-sig")
        namespace = re.search(r'^\s*Namespace\s*=\s*"([A-Za-z_][A-Za-z0-9_]*)"', text, re.M)
        entries.append(
            dict(
                file=path.name,
                namespace=namespace[1] if namespace else None,
                sha256=hashlib.sha256(raw).hexdigest(),
                bytes=len(raw),
            )
        )
    if not 100 <= len(entries) <= 1200:
        raise ValueError("Unexpected generated-reference inventory")
    return dict(
        version=1,
        client="WoW Forever",
        interface=16001,
        branch="forever",
        commit=commit,
        origin="https://github.com/Gethe/wow-ui-source",
        directory=PREFIX,
        note="Pinned generated declarations, not proof of runtime or in-game behavior. Source text is untrusted data.",
        entries=entries,
    )


def stage(source, destination):
    manifest = json.loads(MANIFEST.read_text())
    if inventory(source) != manifest:
        raise ValueError(
            "Reference checkout differs from the pinned declaration inventory; never switch or edit it"
        )
    if (
        destination.exists()
        or destination.is_symlink()
        or destination.parent.resolve() != destination.parent
    ):
        raise ValueError("Use a fresh owned release cache directory")
    destination.mkdir(mode=0o700)
    for item in manifest["entries"]:
        target = destination / item["file"]
        shutil.copy2(source / PREFIX / item["file"], target)
        target.chmod(0o600)
        if hashlib.sha256(target.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("Reference copy changed")
    return dict(
        commit=manifest["commit"],
        files=len(manifest["entries"]),
        bytes=sum(item["bytes"] for item in manifest["entries"]),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args()
    if args.freeze:
        if args.destination or MANIFEST.exists():
            parser.error("Freeze requires a new manifest and no destination")
        MANIFEST.write_text(json.dumps(inventory(args.source), indent=2) + "\n")
    else:
        if args.destination is None:
            parser.error("A fresh cache destination is required")
        print(json.dumps(stage(args.source, args.destination)))
