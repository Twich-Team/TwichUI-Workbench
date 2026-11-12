"""Disposable browser server only; runtime ownership stays outside installed roots."""
import os
from pathlib import Path

from scripts.fixture_ctl import fixture_path
from scripts.reviewed_offer_fixture import everyday_config
from workbench import task_sessions, task_session

path = fixture_path(Path(os.environ['WORKBENCH_DB']))
os.environ['WORKBENCH_PREPARATION_CONFIG'] = str(path.parent / 'automatic-workflow/preparation.json')
root = path.parent / 'controlled-session-owner'
root.mkdir(mode=0o700, exist_ok=True)
task_sessions.ROOT = root
task_session.ROOT = root
everyday_config(path)
from workbench.api import create_app  # noqa: E402

# Source lookup telemetry is controlled in browser fixtures. An unseeded cache
# must never send a real external request from local or hosted acceptance.
from workbench import feature_references  # noqa: E402
_reference_lookup = feature_references.lookup

def _controlled_reference_lookup(engine, request_id, references, **options):
    def no_external_lookup(*args, **kwargs):
        raise ValueError('Controlled browser fixture has no real external reference reader.')
    return _reference_lookup(engine, request_id, references, execute=no_external_lookup)

feature_references.lookup = _controlled_reference_lookup
app = create_app()
