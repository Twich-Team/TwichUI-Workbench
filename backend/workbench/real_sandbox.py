#!/usr/bin/env python3
"""Confinement launcher for owned checks; no privilege or shared-service changes.

Default: Landlock owned read paths/temp writes, all socket creation denied.
Trusted verification: --private-loopback joins the owned cgroup first, then uses
installed bubblewrap to create private user/network/PID namespaces with capabilities
dropped. Landlock and seccomp apply inside, allowing only IPv4/IPv6 socket families;
no host namespace entry, mounts, Unix/vsock/packet sockets or external routes.
--net is an internal second-stage flag and requires a different network namespace.
Failures are closed (97); it never retries unconfined or on the host network.
"""

import ctypes
import os
import platform
import resource
import struct
import sys
from pathlib import Path

SYS = ("/usr", "/bin", "/lib", "/lib64", "/etc/ld.so.cache", "/etc/localtime", "/dev/null", "/dev/urandom")
X, WF, RF, RD = 1, 2, 4, 8


class RS(ctypes.Structure):
    _fields_ = [("fs", ctypes.c_uint64), ("net", ctypes.c_uint64), ("scoped", ctypes.c_uint64)]


def die(why):
    sys.stderr.write("SANDBOX-UNAVAILABLE: %s\n" % why)
    sys.exit(97)


def seccomp(allow_net):
    """Classic-BPF filter built by hand (no libseccomp dependency)."""
    if platform.machine() != "x86_64":
        die("seccomp filter is written for x86_64 only (%s)" % platform.machine())
    LD, JEQ, JSET, RET = 0x20, 0x15, 0x45, 0x06
    ALLOW, KILL, EPERM, EACCES = 0x7FFF0000, 0x80000000, 0x00050000 | 1, 0x00050000 | 13
    deny = [
        101,
        310,
        311,
        438,
        425,
        426,
        427,
        308,  # setns: never enter a host namespace
        272,  # unshare: no nested escape attempts after setup
        165,  # mount: namespace setup is complete
    ]  # ptrace, process_vm_readv/writev, pidfd_getfd, io_uring_setup/enter/register
    ins = [
        (LD, 0, 0, 4),  # arch
        (JEQ, 1, 0, 0xC000003E),
        (RET, 0, 0, KILL),
        (LD, 0, 0, 0),  # syscall nr
        (JSET, 0, 1, 0x40000000),
        (RET, 0, 0, KILL),
    ]  # x32 ABI
    if not allow_net:
        ins += [
            (JEQ, 0, 1, 41),
            (RET, 0, 0, EACCES),
        ]  # socket(): refuse every family (socketpair stays: it cannot reach anything outside)
    if allow_net:
        # Verification may use IPv4/IPv6 in its private namespace only. No pathname
        # Unix sockets, vsock, packet or other host-facing socket families.
        ins += [(JEQ, 0, 5, 41), (LD, 0, 0, 16),
                (JEQ, 2, 0, 2), (JEQ, 1, 0, 10), (RET, 0, 0, EACCES),
                (LD, 0, 0, 0)]
    for n in deny:
        ins += [(JEQ, 0, 1, n), (RET, 0, 0, EPERM)]
    ins += [(RET, 0, 0, ALLOW)]
    buf = b"".join(struct.pack("=HBBI", c, jt, jf, k) for c, jt, jf, k in ins)
    arr = ctypes.create_string_buffer(buf, len(buf))

    class Prog(ctypes.Structure):
        _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.c_void_p)]

    prog = Prog(len(ins), ctypes.addressof(arr))
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(22, 2, ctypes.byref(prog), 0, 0) != 0:
        die("seccomp errno %d" % ctypes.get_errno())


def apply(ro, rw, allow_net=False):
    libc = ctypes.CDLL(None, use_errno=True)
    abi = libc.syscall(444, None, 0, 1)  # landlock_create_ruleset(NULL, 0, VERSION)
    if abi < 1:
        die("kernel has no Landlock (abi %d)" % abi)
    fs = (
        (1 << 13) - 1 | ((1 << 13) if abi >= 2 else 0) | ((1 << 14) if abi >= 3 else 0)
    )  # all fs rights this ABI knows (to bit 14)
    net = 3 if (abi >= 4 and not allow_net) else 0  # handle (= deny) TCP bind+connect
    scoped = 3 if abi >= 6 else 0  # abstract unix sockets + signals to outside the domain
    rs = RS(fs, net, scoped)
    fd = libc.syscall(444, ctypes.byref(rs), ctypes.sizeof(rs) if abi >= 6 else 16, 0)
    if fd < 0:
        die("create_ruleset errno %d" % ctypes.get_errno())

    def allow(path, acc):
        p = os.open(path, os.O_PATH)
        if not os.path.isdir(path):
            acc &= X | WF | RF | (1 << 14)  # file rules only take file rights
        r = libc.syscall(445, fd, 1, ctypes.c_char_p(struct.pack("=Qi", acc & fs, p)), 0)
        os.close(p)
        if r < 0:
            die("add_rule %s errno %d" % (path, ctypes.get_errno()))

    for p in SYS + tuple(ro):
        if os.path.exists(p):
            allow(p, X | RF | RD)
    for p in ("/dev/null",) + tuple(rw):
        allow(p, fs)
    if libc.prctl(38, 1, 0, 0, 0) != 0:
        die("no_new_privs")
    if libc.syscall(446, fd, 0) != 0:
        die("restrict_self errno %d" % ctypes.get_errno())
    os.close(fd)
    seccomp(allow_net)


if __name__ == "__main__":
    a = sys.argv[1:]
    ro, rw, net, cg, fsize, nofile, only = [], [], False, None, None, None, False
    private_loopback = False
    while a and a[0] in (
        "--ro",
        "--rw",
        "--net",
        "--private-loopback",
        "--cgroup",
        "--fsize",
        "--nofile",
        "--cgroup-only",
    ):
        if a[0] == "--private-loopback":
            private_loopback, a = True, a[1:]
            continue
        if a[0] == "--net":
            net, a = True, a[1:]
            continue
        if a[0] == "--cgroup-only":
            only, a = True, a[1:]
            continue
        if a[0] == "--cgroup":
            cg = a[1]
        elif a[0] == "--fsize":
            fsize = int(a[1])
        elif a[0] == "--nofile":
            nofile = int(a[1])
        else:
            (ro if a[0] == "--ro" else rw).append(os.path.realpath(a[1]))
        a = a[2:]
    if not a or a[0] != "--" or len(a) < 2:
        die("usage")
    if cg:  # first thing: from here on every descendant is inside the limited cgroup (memory, pids, cpu) and dies with it
        try:
            open(os.path.join(cg, "cgroup.procs"), "w").write("0")
        except OSError as e:
            die("cannot join cgroup %s: %s" % (cg, e))
    try:
        if fsize:
            resource.setrlimit(resource.RLIMIT_FSIZE, (fsize, fsize))
        if nofile:
            resource.setrlimit(resource.RLIMIT_NOFILE, (nofile, nofile))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (OSError, ValueError) as e:
        die("rlimit: %s" % e)
    if private_loopback:
        # Enter the owned cgroup above BEFORE bubblewrap forks. All helpers inherit limits.
        # Filesystem confinement follows mount setup: Landlock intentionally denies mounts.
        host_net = os.readlink('/proc/self/ns/net')
        env = dict(os.environ, WORKBENCH_VERIFY_HOST_NETNS=host_net)
        inner = ['python3', '-I', str(Path(__file__).resolve()), '--net']
        inner += [x for path in ro for x in ('--ro', path)]
        inner += [x for path in rw for x in ('--rw', path)]
        inner += ['--']+a[1:]
        wrapper = ['/usr/bin/bwrap', '--unshare-user', '--unshare-net', '--unshare-pid',
                   '--die-with-parent', '--new-session', '--cap-drop', 'ALL',
                   '--ro-bind', '/', '/', '--proc', '/proc', '--dev', '/dev']
        wrapper += [x for path in rw for x in ('--bind', path, path)]
        os.execve('/usr/bin/bwrap', wrapper+['--']+inner, env)
    if net:
        host_net = os.environ.pop('WORKBENCH_VERIFY_HOST_NETNS', None)
        if not host_net or os.readlink('/proc/self/ns/net') == host_net:
            die('Networking requires a distinct private verification namespace')
    if not only:
        apply(ro, rw, net)
        if net and os.environ.get('WORKBENCH_VERIFY_PARENT_MARKER'):
            os.environ['WORKBENCH_VERIFY_ACTIVE_NETNS'] = os.readlink('/proc/self/ns/net')
    os.execvp(a[1], a[1:])
