"""Fixed, offline simulator capture; no model-selected programs or live game files."""

from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import time
import tempfile
from uuid import uuid4

from workbench import simulator_comparison as evidence
from workbench.feature_context_storage import read, publish
from workbench.real_workspace import sha
from workbench.regression_preparation import git
from workbench.trusted_preparation import owned
from workbench.visual_scope import digest

BINARY_SHA = "5c769c9b60da3a6196941b1e40206856878e4901111a37fd6268fac512758906"
UI_REVISION = "ca8368f2f2380cbe696854f7f2646387c278a61b"
LIMITATIONS = [
    "Structural simulator evidence only. Addon fonts fall back to Friz Quadrata and game artwork may be absent.",
    "The Forever simulator UI differs from the current game client. Combat, taint, secure actions, addon messaging and reload persistence are not verified.",
    "The isolated lab supplies only two prefix-registration stand-ins; it does not test addon messaging.",
    "Synthetic background countdowns can vary between otherwise equivalent runs; this is not pixel-perfect visual acceptance.",
]

# Fixed lab stand-in, identical for both sides and outside production addon files.
PREFIX_SHIM = """local registered = {}
assert(C_ChatInfo and not C_ChatInfo.RegisterAddonMessagePrefix)
function C_ChatInfo.RegisterAddonMessagePrefix(prefix)
 if type(prefix) ~= "string" or prefix == "" then return 2 end
 if registered[prefix] then return 1 end
 registered[prefix] = true; return 0
end
function C_ChatInfo.IsAddonMessagePrefixRegistered(prefix) return registered[prefix] == true end
"""
SCENARIOS = {
    "notification-appearance": """A_Admin.SetManualClock(0)
UIParent:SetScale(1)
local R = assert(TwichUI, "TwichUI did not initialize")
local ok = R:OpenSettings("notificationsLook")
assert(ok, "Notification settings did not open")
R.SettingsUI.Navigate("notificationsLook", "notifyLookTextScale")
for i=1,40 do A_Admin.AdvanceTime(0.05) end
assert(R.SettingsUI.Selected() == "notificationsLook")
assert(R.SettingsUI.Frame():IsShown() and R.SettingsUI.Frame():GetAlpha() == 1)
assert(R.SettingsUI.View("notificationsLook"), "Page did not construct")
local row = assert(R.SettingsUI.View("notificationsLook").rows.notifyLookTextScale)
assert(row.control.box:GetText() == tostring(R.NotifyLook.Get("shared", "textScale")))
A_Print("WORKBENCH_TEXT_SCALE " .. row.control.box:GetText())
A_Print("WORKBENCH_CAPTURE_READY")
""",
}


def pinned(pin_file):
    pin = json.loads(read(Path(pin_file), 16384))
    lab = owned(Path(pin["lab"]))
    binary = owned(Path(pin["simulator_binary"]))
    expected = lab / "release/wow-ui-sim-v0.1.37-forever-linux-x86_64/wow-sim"
    ui = lab / "xdg/cache/wow-ui-sim/blizzard-ui/wowforever/AddOns"
    if (
        binary != expected
        or pin["simulator_binary_sha256"] != BINARY_SHA
        or sha(binary.read_bytes()) != BINARY_SHA
        or pin["ui_source_pin_head"] != UI_REVISION
    ):
        raise ValueError("The installed simulator differs from the supported pinned profile.")
    provenance = (owned(ui) / ".wow-ui-sim-blizzard-ui-provenance").read_text()
    if (
        "gethe_revision=" + UI_REVISION not in provenance
        or "version=1.60.1.70532" not in provenance
        or "fallback=none" not in provenance
    ):
        raise ValueError("Pinned simulator UI provenance changed.")
    return lab, binary, ui


def stage(source, revision, destination):
    """Only committed addon bytes; reject links, special files and large archives."""
    raw = git(
        source, "archive", revision, "--", "!!!TwichUI", "TwichUI_Settings", max_bytes=64 << 20
    )
    total = 0
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        for item in archive:
            path = Path(item.name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or path.parts[0] not in {"!!!TwichUI", "TwichUI_Settings"}
                or not (item.isdir() or item.isfile())
            ):
                raise ValueError("Simulator source contains an unsupported archive entry.")
            total += item.size
            if total > 64 << 20 or item.size > 8 << 20:
                raise ValueError("Simulator source exceeds its staging bound.")
            target = destination / path
            if item.isdir():
                target.mkdir(mode=0o700, parents=True, exist_ok=True)
            else:
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                publish(target, archive.extractfile(item).read(), limit=8 << 20)
    shim = destination / "!!!!WorkbenchSimulatorPrefixes"
    shim.mkdir(mode=0o700)
    publish(
        shim / "!!!!WorkbenchSimulatorPrefixes.toc",
        b"## Interface: 16001\n## Title: Workbench simulator prefix stand-ins\nprefixes.lua\n",
        limit=1024,
    )
    publish(shim / "prefixes.lua", PREFIX_SHIM.encode(), limit=4096)


def capture(source, revision, root, name, lab, binary, ui, conditions, *, work=None):
    side = (work or root) / name
    side.mkdir(mode=0o700)
    addons = side / "addons"
    addons.mkdir(mode=0o700)
    stage(source, revision, addons)
    state = side / "state"
    state.mkdir(mode=0o700)
    for folder in ["home", "cache", "data", "config", "state", "wtf", "output"]:
        (state / folder).mkdir(mode=0o700)
    scenario = SCENARIOS[conditions["scenario"]].encode()
    publish(side / "scenario.lua", scenario, limit=16384)
    publish(
        side / "input.json",
        json.dumps(
            dict(
                source_revision=revision,
                conditions_binding=digest(conditions),
                scenario_sha256=sha(scenario),
                prefix_stand_in_sha256=sha(PREFIX_SHIM.encode()),
            )
        ).encode(),
        limit=4096,
    )
    unit = "workbench-simulator-" + uuid4().hex
    command = [
        "systemd-run",
        "--user",
        "--wait",
        "--pipe",
        "--quiet",
        "--collect",
        "--unit=" + unit,
        "-p",
        "RuntimeMaxSec=120",
        "-p",
        "MemoryMax=4G",
        "-p",
        "MemorySwapMax=0",
        "-p",
        "TasksMax=128",
        "-p",
        "CPUQuota=200%",
        "-p",
        "UMask=0077",
        "/usr/bin/bwrap",
        "--unshare-all",
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
        "--symlink",
        "usr/bin",
        "/bin",
        "--symlink",
        "usr/lib",
        "/lib",
        "--symlink",
        "usr/lib64",
        "/lib64",
        "--ro-bind",
        str(lab / "release"),
        "/sim/release",
        "--ro-bind",
        str(lab / "deps"),
        "/sim/deps",
        "--ro-bind",
        "/etc/fonts",
        "/etc/fonts",
        "--ro-bind",
        str(addons),
        "/addons",
        "--ro-bind",
        str(side / "scenario.lua"),
        "/scenario.lua",
        "--bind",
        str(state),
        "/state",
        "--ro-bind",
        str(ui),
        "/state/cache/wow-ui-sim/blizzard-ui/wowforever/AddOns",
        "--clearenv",
    ]
    env = dict(
        PATH="/usr/bin:/bin",
        HOME="/state/home",
        LD_LIBRARY_PATH="/sim/deps/extracted/usr/lib/x86_64-linux-gnu",
        XDG_CACHE_HOME="/state/cache",
        XDG_DATA_HOME="/state/data",
        XDG_CONFIG_HOME="/state/config",
        XDG_STATE_HOME="/state/state",
        WOW_SIM_NO_SOUND="1",
        WOW_SIM_CASC="0",
        WOW_SIM_NO_PARTY="1",
        WOW_SIM_SOFTWARE_RENDER="1",
        WOW_SIM_WTF_PATH="/state/wtf",
    )
    for key, value in env.items():
        command += ["--setenv", key, value]
    command += [
        "--chdir",
        "/state/output",
        "/sim/release/" + str(binary.relative_to(lab / "release")),
        "--no-saved-vars",
        "--addons-path",
        "/addons",
        "--exec-lua",
        "@/scenario.lua",
        "screenshot",
        "--png",
        "-o",
        "/state/output/capture.png",
        "--width",
        str(conditions["viewport"][0]),
        "--height",
        str(conditions["viewport"][1]),
    ]
    publish(side / "command.json", json.dumps(command).encode(), limit=16384)
    publish(root / (name + "-command.json"), json.dumps(command).encode(), limit=16384)
    publish(root / (name + "-input.json"), read(side / "input.json", 4096), limit=4096)
    started = time.monotonic()
    with (
        os.fdopen(
            os.open(
                side / "stdout.log",
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                0o600,
            ),
            "wb",
        ) as out,
        os.fdopen(
            os.open(
                side / "stderr.log",
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                0o600,
            ),
            "wb",
        ) as err,
    ):
        host_env = dict(
            PATH="/usr/bin:/bin",
            LANG="C.UTF-8",
            XDG_RUNTIME_DIR="/run/user/" + str(os.getuid()),
            DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/" + str(os.getuid()) + "/bus",
        )
        process = subprocess.Popen(
            command,
            stdout=out,
            stderr=err,
            stdin=subprocess.DEVNULL,
            env=host_env,
        )
        stopped_reason = None
        while process.poll() is None:
            size = count = 0
            for path in state.rglob("*"):
                try:
                    info = path.lstat()
                except FileNotFoundError:
                    continue
                count += 1
                if path.is_file() and not path.is_symlink():
                    size += info.st_size
                if count > 10000 or size > 256 << 20:
                    stopped_reason = "Simulator scratch reached its bounded capture limit."
                    break
            if any(os.fstat(log.fileno()).st_size > 2 << 20 for log in (out, err)):
                stopped_reason = "Simulator diagnostics reached their bounded capture limit."
            if time.monotonic() - started > 135:
                stopped_reason = "Simulator capture exceeded its deadline."
            if stopped_reason:
                # This exact unit was created above; never stop another service.
                subprocess.run(
                    ["systemctl", "--user", "stop", unit],
                    env=host_env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                    check=True,
                )
                process.wait(timeout=10)
                break
            time.sleep(0.1)
        returncode = process.wait(timeout=10)
    diagnostic_bytes = {}
    for stream in ["stdout", "stderr"]:
        path = side / (stream + ".log")
        diagnostic_bytes[stream] = path.stat().st_size
        with path.open("rb") as source:
            data = source.read(2 << 20)
        publish(root / (name + "." + stream), data, limit=2 << 20)
        if diagnostic_bytes[stream] > 2 << 20:
            stopped_reason = "Simulator diagnostics reached their bounded capture limit; full native logs remain in private scratch."
    publish(
        side / "exit.json",
        json.dumps(
            dict(
                returncode=returncode,
                diagnostic_bytes=diagnostic_bytes,
                stopped_reason=stopped_reason,
                elapsed_seconds=time.monotonic() - started,
            )
        ).encode(),
        limit=1024,
    )
    publish(root / (name + "-exit.json"), read(side / "exit.json", 1024), limit=1024)
    stdout = read(root / (name + ".stdout"), 2 << 20)
    stderr = read(root / (name + ".stderr"), 2 << 20)
    if (
        returncode
        or stopped_reason
        or b"WORKBENCH_CAPTURE_READY" not in stdout + stderr
        or b"Lua error:" in stderr
        or b"Lua Error:" in stderr
    ):
        raise ValueError(
            "The isolated scenario failed or reported Lua errors; no comparison is accepted."
        )
    raw = read(state / "output/capture.png", 4 << 20)
    evidence.png(raw, conditions["viewport"])
    publish(root / (name + ".png"), raw, limit=4 << 20)
    return dict(
        sha256=sha(raw),
        bytes=len(raw),
        source_revision=revision,
        conditions_binding=digest(conditions),
        stdout_sha256=sha(stdout),
        stderr_sha256=sha(stderr),
    )


def run(engine, identity, pin_file, scenario="notification-appearance"):
    """Native operator entry point. Exactly one capture pair per checked result."""
    row, grant, root = evidence.coordinates(engine, identity)
    if root is None:
        raise ValueError("Only an actual checked combined feature can receive simulator evidence.")
    from workbench.feature_verification import checked
    from workbench.feature_source import validate_input
    from workbench.trusted_preparation import configuration
    from workbench import everyday

    if everyday.enabled():
        everyday.require_room(everyday.configuration(engine), reserve=256 << 20)

    with engine.connect() as c:
        checked(c, row, grant)
        spec = grant["snapshot"]["feature_verification"]
        validate_input(
            c, spec["feature_input"], grant["snapshot"]["workflow_task"], configuration(engine)
        )
    if scenario not in SCENARIOS:
        raise ValueError("That simulator scenario is not supported.")
    lab, binary, ui = pinned(pin_file)
    conditions = dict(
        simulator="wow-ui-sim v0.1.37 Forever",
        simulator_sha256=BINARY_SHA,
        ui_revision=UI_REVISION,
        ui_version="1.60.1.70532",
        viewport=[1440, 1000],
        ui_scale=1.0,
        state="Clean synthetic solo character, empty WTF, no saved variables, manual clock starts at zero.",
        settings="Original defaults; identical clean profile on both sides.",
        scenario=scenario,
        scenario_sha256=sha(SCENARIOS[scenario].encode()),
        capture_seconds=2,
        network=False,
        game_install_access=False,
    )
    conditions = evidence.CaptureConditions.model_validate(conditions).model_dump(mode="json")
    evidence.create_root(root)  # Existing/interrupted work is never overwritten or replayed.
    if everyday.enabled():
        work_parent = Path(everyday.configuration(engine).scratch)
    else:
        work_parent = Path(configuration(engine)["output_root"]).parent / "simulator-work"
        work_parent.mkdir(mode=0o700, exist_ok=True)
    owned(work_parent)
    if work_parent.stat().st_mode & 0o077:
        raise ValueError("Simulator work requires private scratch storage.")
    work = Path(tempfile.mkdtemp(prefix="workbench-simulator-", dir=work_parent))
    value = dict(
        version=1,
        kind="native-simulator-comparison-v1",
        execution_id=row["id"],
        candidate_id=row["result"]["candidate_id"],
        result_binding=digest(row["result"]),
        captured_at=datetime.now(timezone.utc).isoformat(),
        status="captured",
        conditions=conditions,
        limitations=LIMITATIONS,
    )
    paths = [file["path"] for file in row["result"]["files"]]
    if not any(
        path.startswith("TwichUI_Settings/") or path == "!!!TwichUI/modules/NotifyLook.lua"
        for path in paths
    ):
        value.update(
            status="unsupported",
            before=None,
            after=None,
            limitations=[
                *LIMITATIONS,
                "No capture was attempted: the fixed notification-appearance scenario does not cover these changed files.",
            ],
        )
        evidence.save(root, value, row, grant)
        return evidence.view(engine, identity)
    try:
        for name, source, revision in [
            ("before", Path(spec["original_source"]), spec["original_base"]),
            ("after", Path(spec["feature_input"]["source"]), spec["feature_input"]["commit"]),
        ]:
            value[name] = capture(
                source, revision, root, name, lab, binary, ui, conditions, work=work
            )
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        value.update(
            status="unsupported",
            before=None,
            after=None,
            limitations=[*LIMITATIONS, str(error)[:500]],
        )
    evidence.save(root, value, row, grant)
    return evidence.view(engine, identity)
