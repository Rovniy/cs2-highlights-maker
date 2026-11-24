
"""Launch CS2 through HLAE's custom loader (port of start-counter-strike-with-hlae.ts)."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from highlights.config import app_data_dir
from highlights.recording.sequences import to_unix_path
from highlights.util.log import info
from highlights.util.process import (
    kill_processes,
    wait_for_process_exit,
    wait_for_process_start,
)

GAME_PROCESS = "cs2.exe"

CRASH_ADVICE = (
    "The game crashed during startup (minidump created). The most common cause is an "
    "HLAE incompatible with the current CS2 build: CS2 updates regularly break the "
    "injection until advancedfx ships a new HLAE release (usually within days). "
    "Run `highlights update-tools` to fetch the latest HLAE, or check "
    "https://github.com/advancedfx/advancedfx/releases"
)


class HlaeError(RuntimeError):
    pass


class GameError(RuntimeError):
    pass


def find_minidumps_since(cs2_exe: Path, since_timestamp: float) -> list[Path]:
    """Find CS2 minidumps created since the recording was launched."""
    dumps = []

    for dump in cs2_exe.parent.glob("*.mdmp"):
        try:
            if dump.stat().st_mtime >= since_timestamp:
                dumps.append(dump)
        except OSError:
            continue

    return sorted(dumps, key=lambda path: path.stat().st_mtime)


def build_hlae_args(
    hlae_exe: Path,
    cs2_exe: Path,
    demo_path: Path,
    width: int,
    height: int,
) -> list[str]:
    """Build the HLAE custom-loader command."""

    game_args = (
        f'-insecure -novid +playdemo "{to_unix_path(demo_path)}"'
        f" -width {width} -height {height} -sw"
    )

    return [
        str(hlae_exe),
        "-noGui",
        "-autoStart",
        "-noConfig",
        "-afxDisableSteamStorage",
        "-customLoader",
        "-hookDllPath",
        str(hlae_exe.parent / "x64" / "AfxHookSource2.dll"),
        "-programPath",
        str(cs2_exe),
        "-cmdLine",
        game_args,
    ]


def _hlae_error_window_exists() -> bool:
    """Detect HLAE injection error windows."""

    result = subprocess.run(
        ["tasklist", "/fi", f"imagename eq {GAME_PROCESS}", "/v", "/nh"],
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )

    return "Error - AfxHookSource" in (result.stdout or "")


def run_game_with_hlae(
    hlae_exe: Path,
    cs2_exe: Path,
    demo_path: Path,
    width: int,
    height: int,
    timeout_seconds: float,
) -> None:
    """Launch CS2 through HLAE, monitor recording, and wait for game exit."""

    # Make sure an older CS2 instance is not still running.
    if kill_processes(GAME_PROCESS):
        info("Killed a running CS2 instance, waiting for file locks to release...")
        time.sleep(2)

    # Sandbox the game's user configuration.
    cfg_sandbox = app_data_dir() / "cs2-cfg"
    cfg_sandbox.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ, USRLOCALCSGO=str(cfg_sandbox))

    launch_time = time.time()

    args = build_hlae_args(
        hlae_exe,
        cs2_exe,
        demo_path,
        width,
        height,
    )

    info("Launching CS2 through HLAE...")

    # IMPORTANT FIX:
    # Popen starts HLAE without blocking Python.
    #
    # Previously subprocess.run() waited for HLAE to exit,
    # potentially allowing CS2 to finish and close before
    # Python began monitoring the game process.
    try:
        launcher = subprocess.Popen(
            args,
            env=env,
        )
    except OSError as exc:
        raise HlaeError(f"Failed to launch HLAE: {exc}") from exc

    # Give HLAE time to initialize.
    time.sleep(2)

    # Detect an immediate launcher failure.
    launcher_returncode = launcher.poll()

    if launcher_returncode is not None and launcher_returncode != 0:
        raise HlaeError(
            f"HLAE exited early with code {launcher_returncode}"
        )

    # Detect an HLAE injection error window.
    if _hlae_error_window_exists():
        raise HlaeError(
            "HLAE injection failed (Error - AfxHookSource window detected)"
        )

    def crash_suffix() -> str:
        dumps = find_minidumps_since(cs2_exe, launch_time)

        if not dumps:
            return ""

        return f"\n{CRASH_ADVICE}\nMinidump: {dumps[-1]}"

    # Wait for CS2 to appear while HLAE/game recording runs.
    info("Waiting for CS2 to start...")

    game = wait_for_process_start(
        GAME_PROCESS,
        timeout=30,
    )

    if game is None:
        raise GameError(
            f"CS2 did not start (or exited before detection)."
            f"{crash_suffix()}"
        )

    info(
        "CS2 is running, recording in progress "
        "(the game will close itself when done)..."
    )

    # The recording plugin should execute its scheduled quit
    # command after all highlight sequences finish.
    if not wait_for_process_exit(game, timeout=timeout_seconds):
        kill_processes(GAME_PROCESS)

        raise GameError(
            f"Recording did not finish within "
            f"{int(timeout_seconds)}s watchdog — killed CS2"
        )

    info("CS2 has exited. Checking for crashes...")

    # A normal game exit should not produce a new minidump.
    dumps = find_minidumps_since(cs2_exe, launch_time)

    if dumps:
        raise GameError(
            f"CS2 crashed during recording.{crash_suffix()}"
        )

    info("CS2 exited without a detected crash. Recording stage completed.")
