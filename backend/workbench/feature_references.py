"""Optional external feature references; identities and evidence, never authority.

The fixed source reader is a separate anonymous, read-only operation. Its output
is untrusted project context, never a first-party implementation source or code to
execute. Public source and metadata do not establish Forever compatibility.
"""

from urllib.parse import urlsplit
import re


def identity(value):
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 300
        or value != value.strip()
        or any(ord(c) < 32 for c in value)
    ):
        raise ValueError("Use an addon name or a full GitHub/CurseForge project link.")
    if "://" not in value:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._'\-]{0,79}", value):
            raise ValueError("Use an addon name or paste its full HTTPS project link.")
        return dict(kind="name", identity=value, source_url=None)
    link = urlsplit(value)
    if link.scheme != "https" or link.query or link.fragment or link.username or link.password:
        raise ValueError("Use an HTTPS project link without a sign-in, port, query or fragment.")
    host = link.netloc.lower()
    path = link.path.rstrip("/")
    if host in {"github.com", "www.github.com"}:
        found = re.fullmatch(
            r"/([A-Za-z0-9][A-Za-z0-9-]{0,38})/([A-Za-z0-9_][A-Za-z0-9_.-]{0,99})", path
        )
        if not found:
            raise ValueError(
                "Use the GitHub repository link, without a file, issue or download path."
            )
        owner, name = found.groups()
        if name.endswith(".git"):
            name = name[:-4]
        if not name:
            raise ValueError("The GitHub repository name is missing.")
        name = owner + "/" + name
        return dict(kind="github", identity=name, source_url="https://github.com/" + name)
    if host in {"curseforge.com", "www.curseforge.com"}:
        found = re.fullmatch("/wow/addons/([a-z0-9][a-z0-9-]{0,99})", path)
        if found:
            slug = found[1]
            return dict(
                kind="curseforge",
                identity=slug,
                source_url="https://www.curseforge.com/wow/addons/" + slug,
            )
        raise ValueError(
            "Use the CurseForge WoW addon project link, without a file or download path."
        )
    raise ValueError(
        "Reference links currently support GitHub repositories and CurseForge WoW addon projects."
    )


def identities(references):
    result = []
    seen = set()
    for reference in references:
        item = identity(reference["name_or_url"])
        key = (item["kind"], item["identity"].casefold())
        if key in seen:
            raise ValueError("This reference is listed more than once.")
        seen.add(key)
        result.append(item)
    if len(result) > 4:
        raise ValueError("Use up to four optional reference addons.")
    return result


def _directory(engine, request_id, references, *, create=True):
    from pathlib import Path
    from uuid import UUID
    from workbench.trusted_preparation import configuration, owned
    from workbench.visual_scope import digest

    request_id = str(UUID(str(request_id)))
    descriptors = identities(references)
    base = (
        Path(configuration(engine)["output_root"])
        / "feature-context"
        / "reference-lookups"
        / request_id
    )
    if create:
        base.mkdir(mode=0o700, parents=True, exist_ok=True)
    if base.exists():
        owned(base)
    folder = base / digest(descriptors)
    if create:
        folder.mkdir(mode=0o700, exist_ok=True)
    if folder.exists():
        owned(folder)
    return folder, dict(version=1, request_id=request_id, references=descriptors)


def _read(path, limit=262144):
    import json
    import stat
    from workbench.trusted_preparation import owned

    info = owned(path).stat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_mode & 0o077
        or info.st_nlink != 1
        or info.st_size > limit
    ):
        raise ValueError("Private reference evidence is unavailable or changed.")
    return json.loads(path.read_text())


def _save(path, value):
    import json
    import os
    from uuid import uuid4
    from workbench.trusted_preparation import owned

    data = json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    if len(data) > 262144:
        raise ValueError("Reference evidence exceeds its operation limit.")
    temp = path.parent / (".pending-" + uuid4().hex)
    try:
        fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        try:
            os.link(temp, path, follow_symlinks=False)
        except FileExistsError:
            if owned(path).read_bytes() != data:
                raise ValueError(
                    "Reference evidence already has different immutable content."
                ) from None
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temp.unlink(missing_ok=True)


def lookup(engine, request_id, references, *, execute=None):
    """One finite fixed-host lookup, with immutable attributed evidence.

    There is no provider reservation, model call, downloaded-code execution or
    generated permission. A cached response is read, never silently refreshed.
    """
    import fcntl
    import os
    from pathlib import Path
    import subprocess
    from uuid import uuid4
    from datetime import datetime, timezone
    from workbench.trusted_preparation import owned
    from workbench.visual_scope import digest
    from workbench.storage_resources import require

    if not references:
        return []
    folder, original = _directory(engine, request_id, references)
    lock = folder / "lookup.lock"
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "r+") as locked:
        fcntl.flock(locked, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (folder / "inspected.json").exists():
            return evidence(engine, request_id, references)
        require(folder, folder, 1 << 20)
        _save(folder / "request.json", original)
        script = owned(Path(__file__).resolve().parents[2] / "scripts/feature_reference_lookup.py")
        unit = "workbench-reference-lookup-" + uuid4().hex
        args = [
            "systemd-run",
            "--user",
            "--quiet",
            "--wait",
            "--pipe",
            "--collect",
            "--unit=" + unit,
            "-p",
            "RuntimeMaxSec=100",
            "-p",
            "UMask=0077",
            "-p",
            "MemoryMax=256M",
            "-p",
            "MemorySwapMax=0",
            "-p",
            "CPUQuota=100%",
            "-p",
            "TasksMax=32",
            "-p",
            "NoNewPrivileges=yes",
            "-p",
            "KillMode=control-group",
            "/usr/bin/bwrap",
            "--unshare-user",
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
            "--die-with-parent",
            "--new-session",
            "--cap-drop",
            "ALL",
            "--ro-bind",
            "/usr",
            "/usr",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--bind",
            str(folder),
            "/state",
            "--ro-bind",
            str(script),
            "/service.py",
        ]
        for name in ("/bin", "/lib", "/lib64"):
            path = Path(name)
            if path.is_symlink():
                args += ["--symlink", os.readlink(path), name]
            elif path.exists():
                args += ["--ro-bind", name, name]
        for name in ("/etc/ssl", "/etc/resolv.conf", "/etc/hosts", "/etc/nsswitch.conf"):
            if Path(name).exists():
                args += ["--ro-bind", str(Path(name).resolve()), name]
        args += [
            "--clearenv",
            "--setenv",
            "PATH",
            "/usr/bin:/bin",
            "--setenv",
            "HOME",
            "/tmp/home",
            "--setenv",
            "LANG",
            "C.UTF-8",
            "--chdir",
            "/state",
            "/usr/bin/python3",
            "-I",
            "/service.py",
        ]
        failure = None
        try:
            result = (execute or subprocess.run)(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=110,
                env={
                    "PATH": "/usr/bin:/bin",
                    "XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}",
                    "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/run/user/{os.getuid()}/bus",
                    "LANG": "C.UTF-8",
                },
            )
            if result.returncode or not (folder / "result.json").is_file():
                raise ValueError("Anonymous reference reader was unavailable.")
            data = _read(folder / "result.json")
            from workbench.schemas import FeatureReferenceEvidence

            data["references"] = [
                FeatureReferenceEvidence.model_validate(v).model_dump(mode="json")
                for v in data["references"]
            ]
            if (
                data.get("version") != 1
                or len(data["references"]) != len(original["references"])
                or not 0 <= data["request_count"] <= 16
                or not 0 <= data["received_bytes"] <= 512 * 1024
            ):
                raise ValueError("Reference reader returned unsupported evidence.")
        except subprocess.TimeoutExpired:
            subprocess.run(["systemctl", "--user", "stop", unit], timeout=15, capture_output=True)
            failure = (
                "Reference lookup stopped at its operation limit. No model or code execution began."
            )
        except ValueError, OSError, KeyError, TypeError:
            failure = "Anonymous reference access is unavailable. The original links and instructions remain saved."
        if failure:
            data = dict(
                version=1,
                request_count=None,
                received_bytes=None,
                references=[
                    dict(
                        status="unavailable",
                        provider=r["kind"],
                        name=r["identity"],
                        source_url=r["source_url"],
                        revision=None,
                        license="Not inspected",
                        game_version="Not established",
                        documentation=None,
                        limitations=[failure],
                        inspected_at=datetime.now(timezone.utc).isoformat(),
                        provenance="Attempted fixed-host reference read; no successful inspection established.",
                    )
                    for r in original["references"]
                ],
            )
        receipt = dict(
            version=1,
            input_binding=digest(original),
            result=data,
            evidence_kind="untrusted-reference-data",
            helper_sha256=__import__("hashlib").sha256(script.read_bytes()).hexdigest(),
        )
        _save(folder / "inspected.json", receipt)
    return evidence(engine, request_id, references)


def evidence(engine, request_id, references):
    """Read saved reference evidence only; never perform a lookup from a view."""
    from workbench.visual_scope import digest

    folder, original = _directory(engine, request_id, references, create=False)
    path = folder / "inspected.json"
    if not path.exists():
        return []
    saved = _read(path)
    if (
        saved["input_binding"] != digest(original)
        or saved["evidence_kind"] != "untrusted-reference-data"
    ):
        raise ValueError("Reference evidence belongs to a different request or source.")
    return saved["result"]["references"]


def lookup_view(engine, body):
    from workbench.visual_scope import digest

    values = lookup(engine, body.request_id, [r.model_dump(mode="json") for r in body.references])
    return dict(
        binding=digest(values),
        references=values,
        message="Choose the intended project for ambiguous names. Unavailable sources remain unverified; no code or artwork is copied.",
    )
