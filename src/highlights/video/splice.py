"""Cut a cinematic take into the POV clip of the same play."""

from __future__ import annotations

from pathlib import Path

from highlights.video.ffmpeg import run_ffmpeg
from highlights.video.tiltshift import tilt_filter


def splice_filter(segments: list[tuple[float, float]], tilt: tuple[int, int] | None = None) -> str:
    """Overlay the cinematic video on the POV video while inside any segment.

    Both clips cover the same ticks and share resolution and fps, so the overlay
    is a hard cut; the output keeps the POV timeline (and length). `tilt` (footage width, height)
    gives the cinematic footage a tilt-shift look first."""
    enable = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in segments)
    cinematic = f"[1:v]{tilt_filter(*tilt)}[cin];[0:v][cin]" if tilt else "[0:v][1:v]"
    return f"{cinematic}overlay=enable='{enable}':eof_action=pass,format=yuv420p[v]"


def fade_filter(windows: list[tuple[float, float, float, float]]) -> str:
    """Dissolve the second input over the first around each (start, end, fade_in, fade_out) window.

    The second input is cut to [start - fade_in, end + fade_out], its alpha ramps up over fade_in and
    down over fade_out, and it is overlaid at its own timestamps, so outside the windows the first
    input shows untouched. Alpha ramps on a trimmed stream stay as cheap as a plain overlay."""
    count = len(windows)
    source = "[1:v]format=yuva420p" + (f",split={count}" + "".join(f"[x{i}]" for i in range(count)) if count > 1 else "[x0]")
    parts = [source]
    for i, (start, end, fade_in, fade_out) in enumerate(windows):
        begin, finish = start - fade_in, end + fade_out
        chain = f"[x{i}]trim=start={begin:.3f}:end={finish:.3f},setpts=PTS-STARTPTS+{begin:.3f}/TB"
        if fade_in > 0:
            chain += f",fade=t=in:st={begin:.3f}:d={fade_in:.3f}:alpha=1"
        if fade_out > 0:
            chain += f",fade=t=out:st={end:.3f}:d={fade_out:.3f}:alpha=1"
        parts.append(f"{chain}[w{i}]")
    base = "[0:v]"
    for i in range(count):
        parts.append(f"{base}[w{i}]overlay=eof_action=pass[o{i}]")
        base = f"[o{i}]"
    parts.append(f"{base}format=yuv420p[v]")
    return ";".join(parts)


def splice_clip(
    ffmpeg_exe: Path,
    pov_clip: Path,
    cinematic_clip: Path,
    segments: list[tuple[float, float]],
    output_path: Path,
    crf: int = 18,
    tilt: tuple[int, int] | None = None,
    fades: list[tuple[float, float, float, float]] | None = None,
) -> Path:
    """Write output_path: POV audio throughout, cinematic picture inside the segments.

    `fades` (start, end, fade_in, fade_out) dissolves the second clip in and out instead (x-ray takes)."""
    run_ffmpeg(
        ffmpeg_exe,
        [
            "-y",
            "-i",
            str(pov_clip),
            "-i",
            str(cinematic_clip),
            "-filter_complex",
            fade_filter(fades) if fades else splice_filter(segments, tilt),
            "-map",
            "[v]",
            "-map",
            "0:a:0",
            "-c:v",
            "libx264",
            "-crf",
            str(crf),
            "-c:a",
            "copy",
            str(output_path),
        ],
    )
    return output_path
