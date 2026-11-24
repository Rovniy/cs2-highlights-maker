"""`batch --cinematic-only`: cam-path footage only (no POV), recorded at a high fps and slowed down.

Meant for a teaser/trailer cut to a slow song: per play, one stretch where the cam path sees the player
(kills included), recorded at e.g. 240 fps and played back at a fraction of the speed with every frame
real (240 fps at 0.25x = 60 fps output, no interpolation). No audio: the music goes on in the edit."""

from __future__ import annotations

import json
import math
import shutil
from dataclasses import dataclass, replace
from pathlib import Path

from highlights.config import Config
from highlights.demo.parser import DemoData
from highlights.models import Sequence
from highlights.util.log import info, warn

TAIL = 0.25  # seconds recorded after the window so the last frame is never cut short
MIN_OUTPUT_FPS = 24


@dataclass
class Take:
    name: str
    sequence: Sequence  # the cinematic take, renumbered and ended just after the window
    window: tuple[float, float]  # clip seconds kept
    kills: list[float]  # the POV player's kills (clip seconds)
    label: str
    play: str
    player: str
    round_index: int
    map_name: str


def choose_window(
    segments: list[tuple[float, float]], kills: list[float], length: float
) -> tuple[float, float] | None:
    """The stretch (at most `length` seconds) to keep: the visible segment at, or nearest to, the
    kills, centred on them when it is longer than `length`."""
    if not segments:
        return None
    target = sum(kills) / len(kills) if kills else None

    def gap(segment: tuple[float, float]) -> float:
        if target is None or segment[0] <= target <= segment[1]:
            return 0.0
        return min(abs(segment[0] - target), abs(segment[1] - target))

    a, b = min(segments, key=lambda s: (round(gap(s), 3), -(s[1] - s[0])))
    if b - a <= length:
        return a, b
    centre = (a + b) / 2 if target is None else target
    centre = min(max(centre, a + length / 2), b - length / 2)
    return round(centre - length / 2, 2), round(centre + length / 2, 2)


def output_fps_for(record_fps: int, speed: float, requested: int | None) -> int:
    """Frame rate of the slowed clip: every recorded frame is kept when record_fps x speed allows it."""
    return requested or max(MIN_OUTPUT_FPS, round(record_fps * speed))


def slow_args(window: tuple[float, float], speed: float, fps: int) -> list[str]:
    """ffmpeg output options: cut the window, drop audio, stretch time by 1/speed, resample to `fps`."""
    return [
        "-ss", f"{window[0]:.3f}", "-t", f"{window[1] - window[0]:.3f}", "-an",
        "-vf", f"setpts=PTS/{speed:g},fps={fps}",
        "-c:v", "libx264", "-crf", "14", "-pix_fmt", "yuv420p",
    ]  # fmt: skip


def plan_takes(clips: list, demo: DemoData, length: float, label: str) -> list[Take]:
    """One take per clip whose cam path sees the player; plays no path sees are skipped with a warning."""
    from highlights.recording.cinematic import _kill_times

    takes: list[Take] = []
    for clip in clips:
        cinematic = next((s for s in clip.group if s.cinematic_of is not None and s.splice_segments), None)
        kills = _kill_times(clip.pov, demo.tickrate)
        window = choose_window(cinematic.splice_segments, kills, length) if cinematic else None
        if cinematic is None or window is None:
            warn(f"{label}: no cam path sees {clip.name}, no cinematic take for it")
            continue
        end_tick = min(cinematic.end_tick, cinematic.start_tick + math.ceil((window[1] + TAIL) * demo.tickrate))
        moment = clip.pov.moments[0]
        takes.append(
            Take(
                name=clip.name,
                sequence=replace(cinematic, number=len(takes) + 1, end_tick=end_tick, splice_segments=[window]),
                window=window,
                kills=kills,
                label=label,
                play=" · ".join([moment.describe(), *moment.tags]),
                player=moment.name,
                round_index=moment.round_index,
                map_name=demo.map_name,
            )
        )
    return takes


def take_report(take: Take, speed: float, record_fps: int, output_fps: int, output_seconds: float | None) -> dict:
    a, b = take.window
    return {
        "kind": "teaser",
        "clip": f"{take.name}.mp4",
        "demo": take.label,
        "map": take.map_name,
        "round": take.round_index + 1,
        "player": take.player,
        "play": take.play,
        "cam_path": take.sequence.campath.name if take.sequence.campath else None,
        "source_window_seconds": [a, b],
        "speed": speed,
        "record_fps": record_fps,
        "output_fps": output_fps,
        "output_seconds": None if output_seconds is None else round(output_seconds, 3),
        "kill_times_seconds": [round((k - a) / speed, 3) for k in take.kills if a <= k <= b],
        "audio": "none",
    }


def record_teaser(
    job,
    takes: list[Take],
    config: Config,
    output_dir: Path,
    keep_clips: bool,
    reuse: bool,
    speed: float,
    requested_fps: int | None,
) -> list[Path]:
    """Record the cinematic takes of one demo and write each slowed clip + JSON to `output_dir`."""
    from highlights.batch import record_or_reuse
    from highlights.recording.recorder import RecordingSetupError, resolve_tools
    from highlights.video.ffmpeg import FfmpegError, probe_duration, run_ffmpeg

    fps = config.recording.fps
    out_fps = output_fps_for(fps, speed, requested_fps)
    try:
        outputs = record_or_reuse(job, [t.sequence for t in takes], config, reuse)
    except RecordingSetupError as exc:
        warn(f"{job.label}: recording failed, skipping this demo: {exc}")
        return []
    ffmpeg = resolve_tools(config, auto_install=False).ffmpeg_exe
    video_of = {id(o.sequence): o.video_path for o in outputs}
    written: list[Path] = []
    for take in takes:
        source = video_of.get(id(take.sequence))
        if source is None:
            warn(f"{job.label}: no recording for {take.name}")
            continue
        target = output_dir / f"{take.name}.mp4"
        info(f"{take.name}: {take.window[1] - take.window[0]:.1f}s at {speed:g}x, {fps} -> {out_fps} fps...")
        try:
            run_ffmpeg(ffmpeg, ["-y", "-i", str(source), *slow_args(take.window, speed, out_fps), str(target)])
            seconds = probe_duration(ffmpeg, target)
        except FfmpegError as exc:
            warn(f"{take.name}: slow-motion pass failed: {exc}")
            continue
        report = take_report(take, speed, fps, out_fps, seconds)
        target.with_suffix(".json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        written.append(target)
        info(f"Wrote {target} ({seconds:.1f}s)")
    if not keep_clips:
        shutil.rmtree(job.work_dir, ignore_errors=True)
    return written


def write_plans(takes: list[Take], config: Config, output_dir: Path, speed: float, requested_fps: int | None) -> None:
    """--json-only: the planned windows without recording."""
    fps = config.recording.fps
    out_fps = output_fps_for(fps, speed, requested_fps)
    for take in takes:
        report = take_report(take, speed, fps, out_fps, None)
        (output_dir / f"{take.name}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
