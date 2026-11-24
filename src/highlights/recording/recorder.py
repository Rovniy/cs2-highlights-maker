"""Recording orchestrator: resolve tools, install plugin, run game, verify output."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from highlights.config import Config
from highlights.demo.parser import DemoData
from highlights.models import Sequence
from highlights.recording import plugin, steam, toolchain
from highlights.recording.hlae import run_game_with_hlae
from highlights.recording.sequences import build_actions_file
from highlights.util.log import info, warn


class RecordingSetupError(RuntimeError):
    pass


@dataclass
class SequenceOutput:
    sequence: Sequence
    video_path: Path
    audio_path: Path | None


@dataclass
class ResolvedTools:
    cs2_folder: Path
    cs2_exe: Path
    hlae_exe: Path
    ffmpeg_exe: Path
    plugin_dll: Path


def resolve_tools(config: Config, auto_install: bool = True) -> ResolvedTools:
    if config.paths.cs2_exe != "auto":
        cs2_exe = Path(config.paths.cs2_exe)
        cs2_folder = steam.cs2_folder_from_exe(cs2_exe)
    else:
        found = steam.get_cs2_folder()
        if found is None:
            raise RecordingSetupError(
                "CS2 installation not found. Install CS2 via Steam or set paths.cs2_exe in config."
            )
        cs2_folder = found
        cs2_exe = steam.get_cs2_exe(cs2_folder)
    if not cs2_exe.is_file():
        raise RecordingSetupError(f"cs2.exe not found at {cs2_exe}")

    hlae_exe = toolchain.find_hlae(config)
    if hlae_exe is None:
        if not auto_install:
            raise RecordingSetupError("HLAE not installed (run with auto-install or set paths.hlae_dir)")
        hlae_exe = toolchain.install_hlae()

    ffmpeg_exe = toolchain.find_ffmpeg(config)
    if ffmpeg_exe is None:
        if not auto_install:
            raise RecordingSetupError("FFmpeg not installed (run with auto-install or set paths.ffmpeg_exe)")
        ffmpeg_exe = toolchain.install_ffmpeg()

    plugin_dll = toolchain.find_plugin_dll(config)
    if plugin_dll is None:
        raise RecordingSetupError(
            "CS2 server plugin (server.dll) not found. Either:\n"
            "  1. Install CS Demo Manager (https://cs-demo-manager.com) — its plugin is reused, or\n"
            "  2. Put server.dll into vendor/cs2-plugin/ (build from "
            "https://github.com/akiver/cs-demo-manager cs2-server-plugin/), or\n"
            "  3. Set paths.plugin_dll in config."
        )

    return ResolvedTools(
        cs2_folder=cs2_folder,
        cs2_exe=cs2_exe,
        hlae_exe=hlae_exe,
        ffmpeg_exe=ffmpeg_exe,
        plugin_dll=plugin_dll,
    )


def _watchdog_timeout(sequences: list[Sequence], tickrate: float) -> float:
    clip_seconds = sum(s.duration_seconds(tickrate) for s in sequences)
    # Boot + per-clip seek allowance + clip duration with a generous safety
    # factor (recording runs roughly realtime, encoding may be slower).
    return 120 + len(sequences) * 30 + clip_seconds * 6


def verify_outputs(sequences: list[Sequence], work_dir: Path) -> list[SequenceOutput]:
    outputs: list[SequenceOutput] = []
    missing: list[str] = []
    for sequence in sequences:
        folder = work_dir / sequence.name
        video = folder / "video.mp4"
        if not video.is_file() or video.stat().st_size < 1024:
            missing.append(sequence.name)
            continue
        audio = None
        takes = sorted(folder.glob("take*/audio.wav"))
        if takes:
            audio = takes[-1]
        else:
            warn(f"{sequence.name}: audio.wav not found, the clip will be silent")
        outputs.append(SequenceOutput(sequence=sequence, video_path=video, audio_path=audio))
    if missing:
        warn(f"Missing recorded video for: {', '.join(missing)}")
    return outputs


def record(
    demo: DemoData,
    sequences: list[Sequence],
    config: Config,
    work_dir: Path,
) -> list[SequenceOutput]:
    tools = resolve_tools(config)

    if not steam.is_steam_running():
        raise RecordingSetupError("Steam is not running — start Steam and try again")

    work_dir.mkdir(parents=True, exist_ok=True)
    # The work dir is shared per demo, and the ffmpeg preset has no -y: a leftover
    # video.mp4 from an earlier run (e.g. --keep-clips for another player) would
    # block the new recording and verify_outputs would return the stale file.
    for sequence in sequences:
        shutil.rmtree(work_dir / sequence.name, ignore_errors=True)
    actions = build_actions_file(demo.path, sequences, demo, config.recording, work_dir)
    actions.write()
    toolchain.write_ffmpeg_ini(tools.hlae_exe, tools.ffmpeg_exe)

    info(f"Recording {len(sequences)} sequence(s) — this runs the game, roughly realtime per clip")
    plugin.install_plugin(tools.cs2_folder, tools.plugin_dll)
    try:
        run_game_with_hlae(
            hlae_exe=tools.hlae_exe,
            cs2_exe=tools.cs2_exe,
            demo_path=demo.path,
            width=config.recording.width,
            height=config.recording.height,
            timeout_seconds=_watchdog_timeout(sequences, demo.tickrate),
        )
    finally:
        plugin.uninstall_plugin(tools.cs2_folder)
        actions.delete()

    return verify_outputs(sequences, work_dir)
