#!/usr/bin/env python3
"""Run owned commands in a caller-provided dedicated delegated cgroup.

No automatic bootstrap/reparenting or privileged configuration. Each command has
memory/PID/CPU ceilings with readback and whole-descendant cleanup. Model tools
cannot create sockets; trusted suite verification can request private loopback,
never the host network. The calling worker retains grant/attempt ownership.
"""

import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time

BATCH = os.path.dirname(os.path.abspath(__file__))
SANDBOX = os.path.join(BATCH, "real_sandbox.py")
USAGE_LOG = None  # Explicit run-owned destination required; never a pilot path.
CG = "/sys/fs/cgroup"

# Limits per confined command. Chosen from measured peaks (batch/runs/usage.jsonl; see HANDOFF.md "Resource limits").
LIMITS = {"memory": 6 << 30, "pids": 64, "cpu_pct": 200, "fsize": 16 << 20, "nofile": 1024}
CYCLE_LIMITS = {
    "memory": 16 << 30,
    "pids": 2000,
}  # whole-orchestrator ceiling (applied by systemd-run / the unit)


class Unavailable(RuntimeError):
    pass


_lock, _n = threading.Lock(), [0]


def _own():
    return CG + open("/proc/self/cgroup").read().strip().split("::", 1)[1]


def _w(path, val):
    with open(path, "w") as f:
        f.write(val)


def root():
    r = os.environ.get("WORKBENCH_CG_ROOT")
    if not r or not os.path.isdir(r):
        raise Unavailable(
            "no delegated cgroup (WORKBENCH_CG_ROOT unset): confined execution is unavailable"
        )
    if os.path.realpath(r) != os.path.realpath(_own()) and not os.path.realpath(_own()).startswith(
        os.path.realpath(r) + os.sep
    ):
        raise Unavailable("this process is not inside WORKBENCH_CG_ROOT")
    return r


def _read(p, default=None):
    try:
        return open(p).read().strip()
    except OSError:
        return default


def task_limit_events(cg):
    """Read the shared enclosing scope too: its ceiling may reject a child fork."""
    for line in (_read(os.path.join(cg, 'pids.events'), '') or '').splitlines():
        if line.startswith('max '):
            return int(line.split()[1])
    return 0


def _kill_and_wait(cg, wait=10.0):
    try:
        _w(os.path.join(cg, "cgroup.kill"), "1")
    except OSError:
        pass
    t = time.time()
    while time.time() - t < wait:
        if "populated 0" in (_read(os.path.join(cg, "cgroup.events"), "populated 0") or ""):
            return True
        time.sleep(0.02)
    return False


def run(
    cmd, cwd, timeout, env=None, ro=(), rw=(), net=False, label="cmd", limits=None, sandbox=True,
    private_loopback=False, verification=None, stop_event=None
):
    """-> dict(status, rc, out, secs, usage). status: pass|fail|timeout|oom|error. `sandbox=False` = cgroup limits + cleanup only (simulator).
    Output goes to files on a tmpfs charged to the command's own cgroup, so a runaway writer hits its memory limit, not the disk."""
    if not sandbox:
        return {"status":"error", "rc":None, "out":"Unconfined execution is unavailable", "secs":0, "usage":{}}
    if net:
        return {'status': 'error', 'rc': None, 'out': 'Broad network permission is unavailable', 'secs': 0, 'usage': {}}
    lim = dict(LIMITS, **(limits or {}))
    try:
        from workbench.verification_process import VerificationProcess
        if verification is not None:
            if type(verification) is not VerificationProcess or not private_loopback:
                raise ValueError('Trusted verification capability and private loopback required')
            verification.validate(cmd, cwd, rw)
            lim = dict(lim, fsize=128 << 20, pids=128)
        elif lim['fsize'] > LIMITS['fsize'] or lim['pids'] > LIMITS['pids']:
            raise ValueError('Untrusted command resource ceilings cannot be raised')
        if (lim['memory'] > LIMITS['memory'] or lim['cpu_pct'] > LIMITS['cpu_pct']
                or lim['nofile'] > LIMITS['nofile']):
            raise ValueError('Existing command ceilings cannot be raised')
    except (TypeError, ValueError) as error:
        return dict(status='error', rc=None, out=str(error), secs=0, usage={})
    try:
        base = root()
        parent_task_events = task_limit_events(base)
    except Unavailable as e:
        return {
            "status": "error",
            "rc": None,
            "out": "CONFINEMENT-UNAVAILABLE: %s" % e,
            "secs": 0,
            "usage": {},
        }
    with _lock:
        _n[0] += 1
        cg = os.path.join(base, "cmd-%d-%d" % (os.getpid(), _n[0]))
    scratch = tempfile.mkdtemp(prefix="workbench-real-", dir="/dev/shm")
    t0 = time.time()
    p = None
    marker = None
    status = "error"
    rc = None
    usage = {}
    try:
        os.mkdir(cg)
        _w(cg + "/memory.max", str(lim["memory"]))
        _w(cg + "/memory.swap.max", "0")
        _w(cg + "/memory.oom.group", "1")
        _w(cg + "/pids.max", str(lim["pids"]))
        if not os.path.exists(cg + "/cpu.max"):
            raise Unavailable('CPU ceiling is unavailable; no command started')
        _w(cg + "/cpu.max", "%d 100000" % (lim["cpu_pct"] * 1000))
        for control, expected in {'memory.max': str(lim['memory']), 'memory.swap.max': '0',
                                  'pids.max': str(lim['pids']),
                                  'cpu.max': '%d 100000' % (lim['cpu_pct']*1000)}.items():
            if _read(cg+'/'+control) != expected:
                raise Unavailable('Resource ceiling readback differs: '+control)
        e = dict(env or {})
        for key in ('WORKBENCH_VERIFY_PARENT_MARKER','WORKBENCH_VERIFY_ACTIVE_NETNS'):
            e.pop(key, None)
        if verification is not None:
            marker = socket.socket()
            marker.bind(('127.0.0.1', 0))
            marker.listen(2)
            # Verify the host marker is live, and keep its listening socket open
            # throughout this check. The child must prove it cannot reach it.
            with socket.create_connection(marker.getsockname(), timeout=1):
                accepted, _ = marker.accept()
                accepted.close()
            e['WORKBENCH_VERIFY_PARENT_MARKER'] = json.dumps(dict(
                netns=os.readlink('/proc/self/ns/net'), port=marker.getsockname()[1]))
        if sandbox:
            tmp = os.path.join(scratch, "tmp")
            os.mkdir(tmp)
            e["TMPDIR"] = tmp
            argv = (
                [
                    "python3",
                    "-I",
                    SANDBOX,
                    "--cgroup",
                    cg,
                    "--fsize",
                    str(lim["fsize"]),
                    "--nofile",
                    str(lim["nofile"]),
                ]
                + (["--private-loopback"] if private_loopback else [])
                + [x for p_ in ro for x in ("--ro", p_)]
                + [x for p_ in list(rw) + [tmp] for x in ("--rw", p_)]
                + ["--"]
                + list(cmd)
            )
        else:
            argv = [
                "python3",
                "-I",
                SANDBOX,
                "--cgroup",
                cg,
                "--fsize",
                str(lim["fsize"]),
                "--nofile",
                str(lim["nofile"]),
                "--cgroup-only",
                "--",
            ] + list(cmd)
        so, se = open(scratch + "/out", "w+b"), open(scratch + "/err", "w+b")
        p = subprocess.Popen(
            argv,
            cwd=cwd,
            env=e,
            stdout=so,
            stderr=se,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            while True:
                if stop_event is not None and stop_event.is_set():
                    status = "canceled"
                    break
                try:
                    rc = p.wait(timeout=min(.1, max(.001, timeout-(time.time()-t0))))
                    status = "pass" if rc == 0 else ("crash" if rc < 0 else "fail")
                    break
                except subprocess.TimeoutExpired:
                    if sum(os.fstat(f.fileno()).st_size for f in (so, se)) > 8 << 20:
                        status = 'output_limit'
                        break
                    if time.time()-t0 >= timeout:
                        raise
            if sum(os.fstat(f.fileno()).st_size for f in (so, se)) > 8 << 20:
                status = 'output_limit' 
        except subprocess.TimeoutExpired:
            status = "timeout"
    except Exception as ex:
        status, so, se = "error", None, None
        err = "%s: %s" % (type(ex).__name__, ex)
    finally:
        if marker is not None:
            marker.close()
        if status == "canceled" and p is not None and p.poll() is None:
            # The launcher could still be joining the group. Reap it before
            # treating an empty group as proof there are no more descendants.
            p.kill()
            try:
                p.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
        clean = (
            _kill_and_wait(cg) if os.path.isdir(cg) else True
        )  # reaps every descendant, including daemonised ones
        if p is not None:
            try:
                p.wait(timeout=5)
            except Exception:
                pass
        ev = _read(cg + "/memory.events", "") or ""
        oom = any(line.startswith("oom_kill ") and int(line.split()[1]) > 0 for line in ev.splitlines())
        pev = _read(cg + "/pids.events", "") or ""
        parent_task_delta = max(0, task_limit_events(base) - parent_task_events)
        usage = {
            "label": label,
            "file_limit_bytes": lim["fsize"],
            "trusted_verification": verification is not None,
            "mem_peak_mib": round(int(_read(cg + "/memory.peak", "0")) / 1048576, 1),
            "pids_peak": int(_read(cg + "/pids.peak", "0") or 0),
            "cpu_s": round(
                int(
                    (
                        [
                            line.split()[1]
                            for line in (_read(cg + "/cpu.stat", "") or "").splitlines()
                            if line.startswith("usage_usec")
                        ]
                        or ["0"]
                    )[0]
                )
                / 1e6,
                2,
            ),
            "oom_kill": oom,
            "pids_limit_hit": any(
                line.startswith("max ") and int(line.split()[1]) > 0 for line in pev.splitlines()
            ) or parent_task_delta > 0,
            "enclosing_pids_limit_delta": parent_task_delta,
            "wall_s": round(time.time() - t0, 2),
            "cgroup_removed": False,
            "leaked_after_kill": not clean,
        }
        try:
            os.rmdir(cg)
            usage["cgroup_removed"] = True
        except OSError:
            pass
    out = ""
    streams = {"stdout": "", "stderr": ""}
    if status != "error":
        for f in ("out", "err"):
            try:
                with open(scratch + "/" + f, "rb") as fh:
                    fh.seek(0, 2)
                    fh.seek(max(0, fh.tell() - 8000))
                    tail = fh.read().decode("utf8", "replace")
                    streams["stdout" if f == "out" else "stderr"] = tail
                    out += tail
            except OSError:
                pass
    else:
        out = err
    shutil.rmtree(scratch, ignore_errors=True)
    if usage.get("oom_kill"):
        status = "fail" if status in ("pass", "fail", "crash") else status
        out += (
            "\n[confine] memory limit exceeded (%d MiB): command killed by the cgroup OOM killer"
            % (lim["memory"] >> 20)
        )
    if usage.get("pids_limit_hit"):
        if status == "pass":
            status = "fail"
        out += ("\n[confine] task ceiling reached in command or enclosing scope "
                "(command ceiling %d); inspect enclosing_pids_limit_delta" % lim["pids"])
    if usage.get("leaked_after_kill"):
        status, out = "error", out + "\n[confine] descendants survived cgroup.kill"
    if status in ("pass", "fail", "crash") and rc == 97 and "SANDBOX-UNAVAILABLE" in out:
        status = "error"
    try:
        if not USAGE_LOG:
            return {
                "status": "error",
                "rc": rc,
                "out": "Owned usage log missing",
                "secs": 0,
                "usage": usage,
            }
        os.makedirs(os.path.dirname(USAGE_LOG), exist_ok=True)
        with open(USAGE_LOG, "a") as f:
            f.write(
                json.dumps(dict(usage, status=status, ts=time.strftime("%Y-%m-%dT%H:%M:%S"))) + "\n"
            )
    except OSError:
        pass
    return {
        "status": status,
        "rc": rc,
        "out": out,
        **streams,
        "secs": round(time.time() - t0, 1),
        "usage": usage,
    }
