"""Decide which cam-path shots get a tilt-shift look (--tilt-mode auto|on).

Tilt-shift reads as "miniature" only on wide, elevated shots; a close or ground-level cam path
just looks blurry. Decided from data only (cam path pose vs the player during the spliced
segments), so it can be previewed with --json-only. Generated intro/down-time cams are never
tilted: they are close-ups. The effect itself is an FFmpeg filter on the cinematic footage
before it is overlaid (video/tiltshift.py, video/splice.py).
"""

from __future__ import annotations

from dataclasses import replace
from math import ceil, dist

from highlights.models import Sequence
from highlights.recording.campaths import parse_campath, pose_at, retime
from highlights.recording.cinematic import _target_at

MIN_DISTANCE = 700.0  # average camera-to-player distance (world units) for a shot to count as wide
MIN_HEIGHT = 150.0  # average camera height above the player's feet
MAX_FRACTION = 0.5  # auto: at most this share of the plays (rounded up) gets the look
SAMPLE_STEP = 0.25


def shot_stats(sequence: Sequence, spatial: dict, tickrate: float) -> tuple[float, float] | None:
    """(average distance, average height above the player) of the cam path over the splice
    segments; None when the path or the player's position is unknown."""
    if sequence.campath is None or not sequence.splice_segments:
        return None
    path = parse_campath(sequence.campath)
    if path is None:
        return None
    if sequence.campath_retime:
        path = retime(path, sequence.campath_retime["offset_seconds"], sequence.campath_retime["hold_until_seconds"])
    target_at = _target_at(sequence, spatial, tickrate)
    distances: list[float] = []
    heights: list[float] = []
    for start, end in sequence.splice_segments:
        t = start
        while t <= end:
            pose, target = pose_at(path, t), target_at(t)
            if pose is not None and target is not None:
                distances.append(dist(pose[0], target))
                heights.append(pose[0][2] - target[2])
            t += SAMPLE_STEP
    if not distances:
        return None
    return sum(distances) / len(distances), sum(heights) / len(heights)


def plan_sequences(
    sequences: list[Sequence],
    spatial_by_sequence: dict[int, dict],
    tickrate: float,
    mode: str,
) -> list[Sequence]:
    """Copy of `sequences` with `tilt` set on the cam-path takes chosen for the look.

    `on`: every real cam-path take with splice segments. `auto`: only the wide, elevated ones, best
    first, at most `MAX_FRACTION` of the plays."""
    if mode == "off":
        return sequences
    candidates: list[tuple[float, Sequence, float, float]] = []
    for sequence in sequences:
        if sequence.cinematic_of is None or sequence.dynamic_cam is not None or sequence.static_downtime:
            continue
        stats = shot_stats(sequence, spatial_by_sequence.get(sequence.number, {}), tickrate)
        if stats is None:
            continue
        distance, height = stats
        if mode == "auto" and (distance < MIN_DISTANCE or height < MIN_HEIGHT):
            continue
        candidates.append((distance / 1000 + height / 500, sequence, distance, height))
    if mode == "auto":
        plays = sum(1 for s in sequences if s.cinematic_of is None)
        candidates = sorted(candidates, key=lambda c: c[0], reverse=True)[: ceil(plays * MAX_FRACTION)]
    chosen = {
        id(sequence): {
            "distance": round(distance),
            "height": round(height),
            "reason": "forced" if mode == "on" else f"wide shot, {distance:.0f}u away, {height:.0f}u up",
        }
        for _, sequence, distance, height in candidates
    }
    return [replace(s, tilt=chosen[id(s)]) if id(s) in chosen else s for s in sequences]
