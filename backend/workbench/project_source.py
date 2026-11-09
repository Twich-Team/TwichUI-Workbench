"""Versioned project source, separate from immutable task/result bindings."""
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from uuid import uuid4


def owned(path):
    path=Path(path)
    if (not path.is_absolute() or path.resolve()!=path or path.is_symlink()
            or path.stat().st_uid!=os.getuid()):
        raise ValueError('Project source must use owned paths without symlink traversal.')
    return path


def configured():
    return owned(os.environ['WORKBENCH_RESEARCH_ROOT'])


def registry():
    return configured()/'.git/workbench-source'


def current():
    root=registry()
    if not root.exists():
        return None
    owned(root)
    path=root/'current.json'
    info=owned(path).stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_mode&0o077 or info.st_size>4096:
        raise ValueError('Unsafe current project-source record.')
    value=json.loads(path.read_text())
    if (value.get('version')!=1 or value.get('repository')!='Twich-Team/TwichUI'
            or not isinstance(value.get('base'),str) or not re.fullmatch('[a-f0-9]{40}',value['base'])):
        raise ValueError('Invalid current project-source record.')
    snapshot=owned(root/'snapshots'/value['base'])
    if not snapshot.is_dir():
        raise ValueError('Current project snapshot is unavailable.')
    return value


def active_root():
    value=current()
    return owned(registry()/'snapshots'/value['base']) if value else configured()


def refresh_view(row,jobs,*,has_authority=False):
    if not os.environ.get('WORKBENCH_RESEARCH_ROOT') or row['payload'].get('kind')!='issue' or not row['payload'].get('local_planning'):
        return None
    try:
        value=current()
    except (OSError,ValueError):
        return None  # Source availability cannot hide durable historical data.
    if value is None:
        return None
    inspected=next((j['payload'].get('task_contract',{}).get('resolved_base') or
        j['payload'].get('bug_inspection_packet',{}).get('base') for j in reversed(jobs)
        if j['payload'].get('task_contract',{}).get('resolved_base') or j['payload'].get('bug_inspection_packet',{}).get('base')),None)
    if not inspected:
        return None
    stale=inspected!=value['base']
    eligible=stale and not has_authority and row['status'] in {'completed','failed','waiting_for_information'} and all(j['status'] in {'completed','failed'} for j in jobs)
    from workbench.visual_scope import digest
    binding=digest(dict(original=row['payload'],revision=row['revision'],jobs=[(j['id'],j['status']) for j in jobs],
        inspected=inspected,current=value['base'])) if eligible else None
    return dict(inspected_base=inspected,current_base=value['base'],stale=stale,refresh_binding=binding)


def require_current(contract):
    if not os.environ.get('WORKBENCH_RESEARCH_ROOT') or not contract or not contract.get('resolved_base'):
        return
    try:
        value=current()
    except (OSError,ValueError):
        raise ValueError('Project source is unavailable; repair approval is held until it is restored.') from None
    if value and contract and contract.get('resolved_base') and contract['resolved_base']!=value['base']:
        raise ValueError('This proposal uses older project code. Refresh source and investigate again before approving a repair.')


def retained_root(base):
    """Local objects only. Caller must separately prove a checked result binding."""
    if not isinstance(base,str) or not re.fullmatch('[a-f0-9]{40}',base):
        raise ValueError('Invalid retained project revision.')
    from workbench.regression_preparation import git
    candidates=[configured()]
    path=registry()/'snapshots'/base
    if path.exists():
        candidates.append(owned(path))
    for root in candidates:
        if git(root,'rev-parse','HEAD').decode().strip()==base:
            return root
    raise ValueError('The checked result source snapshot is unavailable.')


def ensure_current():
    """Fixed download operation in a separate, credential-free user sandbox.

    The application's loopback-only network policy is unchanged. Repository
    content and model output cannot choose a URL, command, path or credential.
    Tests/older installations without a source registry do not activate this.
    """
    if not os.environ.get('WORKBENCH_RESEARCH_ROOT') or not registry().exists():
        return None
    state=owned(registry())
    script=Path(__file__).resolve().parents[2]/'scripts/project_source_sync.py'
    owned(script)
    unit='workbench-source-sync-'+uuid4().hex
    args=['systemd-run','--user','--quiet','--wait','--pipe','--collect','--unit='+unit,
          '-p','RuntimeMaxSec=180','-p','MemoryMax=768M','-p','MemorySwapMax=0',
          '-p','CPUQuota=100%','-p','TasksMax=64','-p','NoNewPrivileges=yes',
          '-p','KillMode=control-group','/usr/bin/bwrap','--unshare-user','--unshare-pid',
          '--unshare-ipc','--unshare-uts','--die-with-parent','--new-session','--cap-drop','ALL',
          '--ro-bind','/usr','/usr','--proc','/proc','--dev','/dev','--tmpfs','/tmp',
          '--bind',str(state),'/state','--ro-bind',str(script),'/service.py']
    for name in ('/bin','/lib','/lib64'):
        path=Path(name)
        if path.is_symlink():
            args+=['--symlink',os.readlink(path),name]
        elif path.exists():
            args+=['--ro-bind',name,name]
    for name in ('/etc/ssl','/etc/resolv.conf','/etc/hosts','/etc/nsswitch.conf'):
        if Path(name).exists():
            args+=['--ro-bind',str(Path(name).resolve()),name]
    args+=['--clearenv','--setenv','PATH','/usr/bin:/bin','--setenv','HOME','/tmp/home',
           '--setenv','LANG','C.UTF-8','--chdir','/state','/usr/bin/python3','-I','/service.py']
    try:
        result=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=195,
            env={'PATH':'/usr/bin:/bin','XDG_RUNTIME_DIR':f'/run/user/{os.getuid()}',
                 'DBUS_SESSION_BUS_ADDRESS':f'unix:path=/run/user/{os.getuid()}/bus',
                 'LANG':'C.UTF-8'})
    except subprocess.TimeoutExpired:
        subprocess.run(['systemctl','--user','stop',unit],timeout=15,capture_output=True)
        raise ValueError('Project-source refresh timed out before model generation.') from None
    if result.returncode or len(result.stdout)>4096 or len(result.stderr)>16384:
        raise ValueError('Project-source refresh failed before model generation; retained snapshots were preserved.')
    value=current()
    if value is None:
        raise ValueError('Project refresh returned no verified source snapshot.')
    return value
