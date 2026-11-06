"""Create a fresh frozen stage from an exact Workbench revision and explicit inputs."""

import argparse
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile

from browser_demo import stage as layout
from project_env import ROOT, environment
from workbench.supported_tasks import read_template


def private_json(path, value):
    with path.open("x") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def prepare(args):
    stage = args.stage.absolute()
    if stage.resolve() != stage:
        raise ValueError("Use a new canonical staging directory")
    fixture = args.fixture.absolute()
    if fixture.resolve() != fixture:
        raise ValueError("Use an existing canonical addon checkout")
    task = json.loads(args.task.read_text())
    commit = subprocess.check_output(["git", "rev-parse", "--verify", args.revision + "^{commit}"],
                                     cwd=ROOT, text=True).strip()
    stage.mkdir(mode=0o700)
    archive = subprocess.check_output(["git", "archive", commit], cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(stage, filter="data")
    names = {p.relative_to(stage).as_posix() for p in stage.rglob("*") if p.is_file() or p.is_symlink()}
    toolchain = {}
    for name in (".venv", "node_modules"):
        target = (ROOT / name).resolve(strict=True)
        (stage / name).symlink_to(target, target_is_directory=True)
        toolchain[name] = str(target)
    env = environment() | {"PYTHONPATH": str(stage / "backend"), "PYTHONDONTWRITEBYTECODE": "1"}
    subprocess.run(["npm", "run", "check"], cwd=stage, env=env, check=True)
    for folder in (layout.PREPARATIONS, layout.TEMPLATES, layout.PROOF.parent):
        (stage / folder).mkdir(mode=0o700, parents=True)
    for template in args.template:
        data = read_template(template.absolute().parent, template.stem)
        if not data or template.name != template.stem + ".json":
            raise ValueError("A supported template must be a validated hash-named JSON file")
        shutil.copyfile(template, stage / layout.TEMPLATES / template.name)
    shutil.copyfile(args.proof, stage / layout.PROOF)
    db = stage / layout.DATABASE
    subprocess.run([str(stage / ".venv/bin/python"), "-c",
        "import sys; from alembic import command; from alembic.config import Config; "
        "from workbench.db import engine_for; e=engine_for(sys.argv[1]); c=e.connect(); "
        "cfg=Config('alembic.ini'); cfg.attributes['connection']=c; command.upgrade(cfg,'head'); c.close(); e.dispose()",
        str(db)], cwd=stage, env=env, check=True)
    shutil.copyfile(db, stage / layout.BACKUP)
    binding = dict(database=str(db), database_identity=[db.stat().st_dev, db.stat().st_ino],
                   repository="Twich-Team/TwichUI")
    private_json(stage / layout.POLICY, dict(**binding, version=1, allowed_classes=["lua-regression-tests"],
        output_root=str(stage / layout.PREPARATIONS), identity=dict(name=args.author, email=args.email),
        scenario_generation=True))
    private_json(stage / layout.PENDING, dict(**binding, mode="explicit-one-off-only",
        templates_root=str(stage / layout.TEMPLATES),
        templates=sorted(p.name for p in (stage / layout.TEMPLATES).iterdir()),
        base_branch="main", model="pilot-qwen38-27b", automatic_fallback=False,
        total_limit_proof=str(stage / layout.PROOF), total_limit_proof_sha256=layout.digest(stage / layout.PROOF)))
    names |= layout.files(stage, layout.STATIC) | layout.files(stage, layout.TEMPLATES)
    names |= {str(p) for p in (layout.DATABASE, layout.BACKUP, layout.POLICY, layout.PENDING, layout.PROOF)}
    manifest = dict(version=2, stage=str(stage), checkpoint=commit, fixture=str(fixture), fixture_base=task["base"],
        task=task, window_minutes=105, budget_reserve_minutes=72, toolchain=toolchain,
        counted_roots=sorted({str(ROOT), *(str(root.resolve()) for root in args.counted_root)}),
        history={str(p.resolve()): layout.digest(p) for p in args.history},
        preserve_checkout=str(args.preserve_checkout.resolve()) if args.preserve_checkout else None,
        mode="live" if args.installation else "offline",
        sha256={name: layout.digest(stage / name) for name in sorted(names)})
    if args.installation:
        installed = json.loads(args.installation.read_text())
        manifest.update(model_identity=installed["model_identity"], local_key_path=installed["local_key_path"])
    path = stage / layout.MANIFEST
    private_json(path, manifest)
    fingerprint = layout.digest(path)
    subprocess.run([str(stage / ".venv/bin/python"), "-m", "browser_demo.live", str(path),
                    fingerprint, "--preflight"], cwd=stage, env=env | {
                        "PYTHONPATH": f"{stage / 'scripts'}:{stage / 'backend'}"}, check=True)
    return fingerprint


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("stage", "fixture", "task", "proof"):
        parser.add_argument(name, type=Path)
    parser.add_argument("--revision", default="HEAD")
    parser.add_argument("--installation", type=Path, help="Established model identity and credential path only")
    parser.add_argument("--author", default="TwichUI Workbench")
    parser.add_argument("--email", default="workbench@local.invalid")
    parser.add_argument("--template", type=Path, action="append", default=[])
    parser.add_argument("--counted-root", type=Path, action="append", default=[])
    parser.add_argument("--history", type=Path, action="append", default=[])
    parser.add_argument("--preserve-checkout", type=Path)
    os.umask(0o077)
    print(prepare(parser.parse_args()), flush=True)


if __name__ == "__main__":
    main()
