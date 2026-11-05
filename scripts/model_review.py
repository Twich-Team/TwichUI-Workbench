"""A new isolated disposable Models review. Never restores or changes an existing store.

Start with the project Python in a detached process. Stop via its exact saved
identity using --stop. Normal shutdown retains this review's own isolated DB.
No reboot/service supervision is installed.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
from uuid import uuid4

from project_env import environment, require_ports

ROOT = Path(__file__).resolve().parents[1]

FOLDER = ROOT / '.local/m4-review'
IDENTITY = FOLDER / 'identity.json'


def stop():
    identity = json.loads(IDENTITY.read_text())
    pid = identity['pid']
    try:
        fd = os.pidfd_open(pid)
    except ProcessLookupError:
        print('M4 review already stopped; no signal sent.')
        return
    try:
        proc = Path('/proc') / str(pid)
        try:
            matches = (Path('/proc/sys/kernel/random/boot_id').read_text() == identity['boot_id']
                and proc.stat().st_uid == os.getuid()
                and (proc/'stat').read_text().split(') ', 1)[1].split()[19] == identity['start']
                and (proc/'cmdline').read_bytes().hex() == identity['argv']
                and (proc/'cwd').resolve() == ROOT)
        except FileNotFoundError:
            matches = False
        if not matches:
            raise SystemExit('Identity changed; no signal sent. Inspect before proceeding.')
        signal.pidfd_send_signal(fd, signal.SIGTERM)
        print('Stopped verified M4 supervisor only; it stops its owned API/demo worker. Isolated review data is retained.')
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stop', action='store_true')
    args = parser.parse_args()
    if args.stop:
        stop()
        return
    os.chdir(ROOT)
    os.umask(0o077)
    require_ports([9387])
    # A repeated start cannot erase a prior review or its owner changes.
    FOLDER.mkdir(mode=0o700, parents=True, exist_ok=False)
    shutil.copytree(ROOT/'frontend/dist', FOLDER/'static')
    sys.path.insert(0, str(ROOT/'backend'))
    from alembic import command
    from alembic.config import Config
    from workbench.db import engine_for
    from workbench.seed import seed
    from workbench import intake, planning_worker
    from workbench.schemas import IntakeInput
    from workbench.planning_provider import DemoProvider
    path = FOLDER/'review.sqlite3'
    engine = engine_for(path)
    with engine.connect() as connection:
        config = Config(str(ROOT/'alembic.ini'))
        config.attributes['connection'] = connection
        command.upgrade(config, 'head')
        connection.commit()
    seed(engine)
    intake.submit(engine, IntakeInput(request_id=str(uuid4()), kind='issue', title='Synthetic Models accounting example',
        description='Demonstrate one durable demo planning attempt. No provider inference, addon implementation or publication is authorized.',
        lua_error='', visual_requirement='nonvisual'))
    planning_worker.process_one(engine, DemoProvider())
    engine.dispose()
    proc = Path('/proc')/str(os.getpid())
    IDENTITY.write_text(json.dumps(dict(pid=os.getpid(), boot_id=Path('/proc/sys/kernel/random/boot_id').read_text(),
        start=(proc/'stat').read_text().split(') ', 1)[1].split()[19], argv=(proc/'cmdline').read_bytes().hex(),
        url='http://127.0.0.1:9387/models', database=str(path), static_root=str(FOLDER/'static')), indent=2))
    env = environment()
    # No implicit access to any existing runtime source, credential or provider.
    for key in list(env):
        if key.startswith('WORKBENCH_'):
            env.pop(key)
    env.update(WORKBENCH_DB=str(path), WORKBENCH_PLANNER='demo',
        WORKBENCH_CAPABILITIES=str(ROOT/'.local/m4-capabilities.json'), WORKBENCH_STATIC_ROOT=str(FOLDER/'static'))
    stopped = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopped.set())
    children = []
    try:
        for args in ([sys.executable, '-m', 'uvicorn', 'workbench.api:app', '--host', '127.0.0.1', '--port', '9387', '--no-proxy-headers'],
                     [sys.executable, '-m', 'workbench.worker']):
            children.append(subprocess.Popen(args, env=env, stdin=subprocess.DEVNULL))
        while not stopped.wait(.5):
            if any(child.poll() is not None for child in children):
                raise RuntimeError('Owned review child exited; stopping only owned children.')
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        (FOLDER/'stopped.json').write_text(json.dumps({'status':'stopped','database_retained':True}))


if __name__ == '__main__':
    main()
