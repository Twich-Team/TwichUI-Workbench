"""Project-local tool discovery; never writes host profiles or installs packages."""
import json
import os
from pathlib import Path
import shutil
import socket
import tomllib

ROOT = Path(__file__).resolve().parents[1]
CODEX = Path('/home/rich/.local/bin/codex')
# Historical reporting compatibility only. Active admission uses physical
# recovery room and verified retention, not this former delivery allowance.
ARTIFACT_LIMIT = 512 * 1024 * 1024


def physical_headroom(paths=(ROOT, Path('/dev/shm'))):
    """Usable physical space beyond recovery reserve, never a campaign total."""
    return min(shutil.disk_usage(path).free for path in paths)-(4 << 30)


def environment():
    env = dict(os.environ)
    if not shutil.which('node') or not shutil.which('npm'):
        fallback = Path('/tmp/twichui-workbench-tools/bin')
        if (fallback / 'node').exists() and (fallback / 'npm').exists():
            env['PATH'] = str(fallback) + os.pathsep + env.get('PATH', '')
    browsers = Path('/tmp/twichui-dashboard-browsers')
    libraries = Path('/tmp/twichui-dashboard-libs/root/usr/lib/x86_64-linux-gnu')
    if 'PLAYWRIGHT_BROWSERS_PATH' not in env and browsers.is_dir():
        env['PLAYWRIGHT_BROWSERS_PATH'] = str(browsers)
    if libraries.is_dir():
        env['LD_LIBRARY_PATH'] = str(libraries) + (
            os.pathsep + env['LD_LIBRARY_PATH'] if env.get('LD_LIBRARY_PATH') else '')
    env['NO_PROXY'] = '127.0.0.1,localhost'
    env['no_proxy'] = env['NO_PROXY']
    env['PYTHONPATH'] = str(ROOT / 'backend')
    return env


def config_arguments():
    config = tomllib.loads((ROOT / 'tools/codex-workbench.toml').read_text())
    def toml_value(value):
        if isinstance(value, dict):
            return '{' + ','.join(json.dumps(key) + '=' + toml_value(child)
                                  for key, child in value.items()) + '}'
        return json.dumps(value)
    result = []
    for key, value in config.items():
        result.extend(['-c', key + '=' + toml_value(value)])
    return result



def artifact_bytes(root=ROOT):
    total = 0
    for name in ['artifacts', 'test-results', 'playwright-report', '.local/runs']:
        base = root / name
        if base.is_symlink():
            raise RuntimeError('Artifact root must not be a symlink')
        if base.exists():
            for path in base.rglob('*'):
                if path.is_file() and not path.is_symlink():
                    total += path.stat().st_size
    return total


def require_ports(ports):
    for port in ports:
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(('127.0.0.1', port))
            except PermissionError as error:
                raise RuntimeError("Sandbox policy denied a loopback port probe; no escalation was attempted.") from error
            except OSError as error:
                raise RuntimeError(f'Port {port} is occupied; no service will be replaced.') from error
