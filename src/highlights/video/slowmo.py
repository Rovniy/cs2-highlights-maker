"""Slow-motion window inside a finished clip."""

from __future__ import annotations

from pathlib import Path

from highlights.video.ffmpeg import run_ffmpeg


def atempo_chain(speed: float) -> str:
    """atempo only accepts 0.5-100 per stage, so slower speeds are chained."""
    stages = []
    while speed < 0.5:
        stages.append("atempo=0.5")
        speed /= 0.5
    stages.append(f"atempo={speed:.4f}")
    return ",".join(stages)


def slowmo_filter(start: float, end: float, speed: float) -> str:
    """Filter graph: [v]/[a] play normally except start..end, which runs at `speed`."""
    parts = [(0.0, start, 1.0), (start, end, speed), (end, None, 1.0)]
    parts = [(a, b, s) for a, b, s in parts if b is None or b - a > 0.01]
    video = [f"[0:v]split={len(parts)}" + "".join(f"[v{i}]" for i in range(len(parts)))]
    audio = [f"[0:a]asplit={len(parts)}" + "".join(f"[a{i}]" for i in range(len(parts)))]
    for i, (a, b, s) in enumerate(parts):
        span = f"start={a:.3f}" + (f":end={b:.3f}" if b is not None else "")
        retime = "" if s == 1.0 else f"/{s:.4f}"
        video.append(f"[v{i}]trim={span},setpts=(PTS-STARTPTS){retime}[sv{i}]")
        tempo = "" if s == 1.0 else f",{atempo_chain(s)}"
        audio.append(f"[a{i}]atrim={span},asetpts=PTS-STARTPTS{tempo}[sa{i}]")
    joined = "".join(f"[sv{i}][sa{i}]" for i in range(len(parts)))
    return ";".join([*video, *audio, f"{joined}concat=n={len(parts)}:v=1:a=1[v][a]"])


def slow_clip(
    ffmpeg_exe: Path, clip: Path, output_path: Path, start: float, end: float, speed: float, crf: int = 18
) -> Path:
    """Write output_path: `clip` with start..end (clip seconds) slowed to `speed`."""
    run_ffmpeg(
        ffmpeg_exe,
        [
            "-y",
            "-i",
            str(clip),
            "-filter_complex",
            slowmo_filter(start, end, speed),
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-c:v",
            "libx264",
            "-crf",
            str(crf),
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-ac",
            "2",
            str(output_path),
        ],
    )
    return output_path
