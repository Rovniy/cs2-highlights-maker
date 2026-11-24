"""Post-recording review: flag cam-path footage that looks like the camera is inside a wall, and
make a contact sheet per clip plus an index page for a quick look over a whole output folder.

Only the cam-path parts are checked (cinematic and generated-cam splice segments, B-roll and
build-up clips); POV footage never is. The check is a heuristic on tiny grayscale frames: a camera
inside geometry tends to show a near-black frame or one big featureless surface. It can miss
see-through clipping and can flag a legitimately plain shot (a wall filling the frame on purpose).
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from statistics import pstdev

from highlights.util.log import info, warn
from highlights.video.ffmpeg import FfmpegError, probe_duration, run_ffmpeg
from highlights.video.frames import gray_frames

SAMPLE_FPS = 4
FRAME_W, FRAME_H = 64, 36
BLOCK = 4  # flatness is measured on BLOCK x BLOCK pixel blocks
FLAT_BLOCK_STD = 2.5  # a block this uniform counts as featureless
FLAT_FRACTION = 0.75  # this share of featureless blocks makes the frame suspect
DARK_MEAN = 10  # a frame this dark on average is suspect
SUSPECT_RUN = 2  # consecutive suspect samples (0.5 s) before a range is reported
SHEET_COLUMNS, SHEET_ROWS, SHEET_WIDTH = 6, 2, 320


def frame_problem(frame: bytes, width: int = FRAME_W, height: int = FRAME_H) -> str | None:
    """"dark", "flat" or None for one gray frame."""
    if sum(frame) / len(frame) < DARK_MEAN:
        return "dark"
    flat = total = 0
    for by in range(0, height - BLOCK + 1, BLOCK):
        for bx in range(0, width - BLOCK + 1, BLOCK):
            values = [frame[(by + y) * width + bx + x] for y in range(BLOCK) for x in range(BLOCK)]
            total += 1
            flat += pstdev(values) < FLAT_BLOCK_STD
    return "flat" if total and flat / total >= FLAT_FRACTION else None


def suspect_ranges(
    problems: list[str | None], ranges: list[tuple[float, float]], fps: float = SAMPLE_FPS
) -> list[dict]:
    """Runs of >= SUSPECT_RUN suspect samples inside the checked ranges, as {start, end, reason}."""
    found: list[dict] = []
    run: list[tuple[float, str]] = []

    def close() -> None:
        if len(run) >= SUSPECT_RUN:
            reasons = {r for _, r in run}
            found.append({
                "start_seconds": round(run[0][0], 2),
                "end_seconds": round(run[-1][0] + 1 / fps, 2),
                "reason": "dark" if reasons == {"dark"} else "flat",
            })
        run.clear()

    for index, problem in enumerate(problems):
        t = index / fps
        inside = any(a <= t < b for a, b in ranges)
        if inside and problem:
            run.append((t, problem))
        else:
            close()
    close()
    return found


def checked_ranges(report: dict, duration: float) -> list[tuple[float, float]]:
    """Cam-path ranges of a clip from its JSON: the whole clip for B-roll/build-up, else the splice
    segments of its cinematic and generated-cam takes."""
    if report.get("kind") in ("broll", "buildup", "teaser"):
        return [(0.0, duration)]
    ranges = []
    for sequence in report.get("sequences", []):
        cinematic = sequence.get("cinematic") or {}
        for segment in cinematic.get("splice_segments") or []:
            ranges.append((segment["start_seconds"], segment["end_seconds"]))
    return sorted(ranges)


def contact_sheet(ffmpeg_exe: Path, clip: Path, out: Path, duration: float) -> Path:
    tiles = SHEET_COLUMNS * SHEET_ROWS
    rate = tiles / max(duration, 0.1)
    out.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        ffmpeg_exe,
        [
            "-y", "-i", str(clip),
            "-vf", f"fps={rate:.4f},scale={SHEET_WIDTH}:-2,tile={SHEET_COLUMNS}x{SHEET_ROWS}",
            "-frames:v", "1", "-q:v", "4", str(out),
        ],
    )
    return out


def review_clip(ffmpeg_exe: Path, clip: Path, report: dict) -> dict:
    duration = probe_duration(ffmpeg_exe, clip)
    ranges = checked_ranges(report, duration)
    suspects: list[dict] = []
    if ranges:
        frames = gray_frames(ffmpeg_exe, clip, SAMPLE_FPS, FRAME_W, FRAME_H)
        suspects = suspect_ranges([frame_problem(f) for f in frames], ranges)
    return {
        "checked_ranges": [{"start_seconds": round(a, 2), "end_seconds": round(b, 2)} for a, b in ranges],
        "suspect_ranges": suspects,
        "method": "heuristic: dark or featureless frames in cam-path footage",
        "duration_seconds": round(duration, 3),
    }


def review_dir(ffmpeg_exe: Path, directory: Path, names: list[str] | None = None) -> list[dict]:
    """Review every clip with a JSON report in `directory` (or only `names`); stores the result as
    `review` in each JSON, writes review/<name>.jpg and review/index.html. Returns the results."""
    sheets = directory / "review"
    results = []
    for clip in sorted(directory.glob("*.mp4")):
        report_path = clip.with_suffix(".json")
        if (names is not None and clip.stem not in names) or not report_path.is_file():
            continue
        report = json.loads(report_path.read_text(encoding="utf-8"))
        try:
            review = review_clip(ffmpeg_exe, clip, report)
            contact_sheet(ffmpeg_exe, clip, sheets / f"{clip.stem}.jpg", review["duration_seconds"])
        except FfmpegError as exc:
            warn(f"{clip.name}: review failed: {exc}")
            continue
        report["review"] = review
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        for suspect in review["suspect_ranges"]:
            warn(
                f"{clip.name}: cam-path footage looks {suspect['reason']} at "
                f"{suspect['start_seconds']:.1f}-{suspect['end_seconds']:.1f}s — possible clipping, check it"
            )
        results.append({"name": clip.stem, **review})
    write_index(sheets / "index.html", results)
    if results:
        flagged = sum(1 for r in results if r["suspect_ranges"])
        info(f"Review: {len(results)} clip(s), {flagged} flagged — open {sheets / 'index.html'}")
    return results


def write_index(path: Path, results: list[dict]) -> None:
    rows = []
    for r in results:
        name = html.escape(r["name"])
        if r["suspect_ranges"]:
            note = "; ".join(
                f"{s['reason']} {s['start_seconds']:.1f}–{s['end_seconds']:.1f}s" for s in r["suspect_ranges"]
            )
            status = f'<span class="bad">check: {html.escape(note)}</span>'
        elif r["checked_ranges"]:
            status = '<span class="ok">cam-path parts look fine</span>'
        else:
            status = '<span class="muted">no cam-path parts</span>'
        rows.append(
            f'<section><h2>{name}.mp4</h2><p>{status} · {r["duration_seconds"]:.1f}s</p>'
            f'<a href="../{name}.mp4"><img src="{name}.jpg" alt="{name} frames"></a></section>'
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "<!doctype html><meta charset='utf-8'><title>Clip review</title><style>"
        ":root{--bg:#fff;--fg:#1a1a1a;--muted:#666;--bad:#b3261e;--ok:#1b6e3a}"
        "@media (prefers-color-scheme:dark){:root{--bg:#141414;--fg:#eee;--muted:#999;--bad:#ff8a80;--ok:#7bd88f}}"
        "body{background:var(--bg);color:var(--fg);font:15px system-ui,sans-serif;margin:0 auto;padding:16px;max-width:1960px}"
        "h2{font-size:16px;margin:24px 0 4px}img{max-width:100%;height:auto;display:block}"
        ".bad{color:var(--bad);font-weight:600}.ok{color:var(--ok)}.muted{color:var(--muted)}</style>"
        f"<h1>Clip review</h1><p class='muted'>{len(results)} clip(s). Frames are spread evenly over each clip; "
        "flags are a heuristic on cam-path parts only.</p>" + "".join(rows),
        encoding="utf-8",
    )
