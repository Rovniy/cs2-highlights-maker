"""Vertical (9:16) version of the finished video for Shorts / Reels / TikTok."""

from __future__ import annotations

from pathlib import Path

from highlights.video.ffmpeg import run_ffmpeg

WIDTH = 1080
HEIGHT = 1920

# The CS2 kill feed sits in the top-right corner (fractions of the 16:9 frame).
KILLFEED_X = 0.60
KILLFEED_Y = 0.02
KILLFEED_W = 0.40
KILLFEED_H = 0.26
FOOTAGE_HEIGHT = 608  # a 16:9 frame scaled to WIDTH
KILLFEED_GAP = 40  # pixels between the footage and the kill-feed strip


def vertical_filter(mode: str, killfeed: bool = False) -> str:
    """Filter graph (input 0 = the 16:9 video, output [v]) for a 1080x1920 frame.

    blur: the footage fits the full width over a blurred, zoomed copy of itself; with
    `killfeed` the top-right kill feed is cut out and shown larger below the footage.
    crop: the centre 9:16 slice of the frame, scaled up (loses the kill feed and sides).
    """
    if mode == "crop":
        return f"[0:v]crop=trunc(ih*9/16/2)*2:ih,scale={WIDTH}:{HEIGHT},format=yuv420p[v]"
    if mode != "blur":
        raise ValueError(f"unknown vertical mode {mode!r} (expected blur or crop)")
    base = (
        f"[bgsrc]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH}:{HEIGHT},boxblur=24:3[bg];"
        f"[fgsrc]scale={WIDTH}:-2[fg];"
    )
    if not killfeed:
        return f"[0:v]split=2[bgsrc][fgsrc];{base}[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p[v]"
    strip = (
        f"[kfsrc]crop=iw*{KILLFEED_W}:ih*{KILLFEED_H}:iw*{KILLFEED_X}:ih*{KILLFEED_Y},"
        f"scale={WIDTH}:-2[kf];"
    )
    return (
        f"[0:v]split=3[bgsrc][fgsrc][kfsrc];{base}{strip}"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2[mid];"
        f"[mid][kf]overlay=0:{(HEIGHT + FOOTAGE_HEIGHT) // 2 + KILLFEED_GAP},format=yuv420p[v]"
    )


def make_vertical(
    ffmpeg_exe: Path,
    source: Path,
    output_path: Path,
    mode: str = "blur",
    killfeed: bool = False,
    crf: int = 20,
) -> Path:
    """Write the 1080x1920 version of `source` (audio copied)."""
    run_ffmpeg(
        ffmpeg_exe,
        [
            "-y",
            "-i",
            str(source),
            "-filter_complex",
            vertical_filter(mode, killfeed),
            "-map",
            "[v]",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-crf",
            str(crf),
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            str(output_path),
        ],
    )
    return output_path
