"""Disposable browser server only; runtime ownership stays outside installed roots."""
import os
from pathlib import Path

from scripts.fixture_ctl import fixture_path
from workbench import task_sessions, task_session

path = fixture_path(Path(os.environ['WORKBENCH_DB']))
root = path.parent / 'controlled-session-owner'
root.mkdir(mode=0o700, exist_ok=True)
task_sessions.ROOT = root
task_session.ROOT = root
from workbench.api import create_app  # noqa: E402

app = create_app()
