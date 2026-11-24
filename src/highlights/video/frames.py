"""Small grayscale frames out of a clip, for cheap image checks in Python."""

from __future__ import annotations

import subprocess
from pathlib import Path

from highlights.video.ffmpeg import FfmpegError


def gray_frames(
    ffmpeg_exe: Path,
    clip: Path,
    fps: float,
    width: int,
    height: int,
    crop: tuple[float, float, float, float] | None = None,
) -> list[bytes]:
    """Frames sampled at `fps`, optionally cropped to (x, y, w, h) fractions of the frame, scaled to
    width x height, 8-bit gray, one bytes object per frame."""
    filters = []
    if crop:
        x, y, w, h = crop
        filters.append(f"crop=iw*{w}:ih*{h}:iw*{x}:ih*{y}")
    filters += [f"fps={fps:g}", f"scale={width}:{height}", "format=gray"]
    cmd = [
        str(ffmpeg_exe), "-hide_banner", "-loglevel", "error", "-i", str(clip),
        "-vf", ",".join(filters), "-f", "rawvideo", "-",
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise FfmpegError(f"ffmpeg frame extraction failed: {result.stderr.decode(errors='replace')[-500:]}")
    size = width * height
    data = result.stdout
    return [data[i : i + size] for i in range(0, len(data) - size + 1, size)]
