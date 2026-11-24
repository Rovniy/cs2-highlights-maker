"""Measure when kills really happen in a recorded POV clip, from new kill-feed rows appearing.

The report's kill times assume the capture starts exactly at start_tick. Each new kill-feed row is
a sudden change in the kill-feed corner, so the clip's change spikes are matched against the
nominal kill times and their consistent shift is the clip's timing offset. Only used when the
match is unambiguous; otherwise the nominal times stay (and the report says so).
"""

from __future__ import annotations

from math import ceil
from pathlib import Path
from statistics import median

from highlights.video.frames import gray_frames
from highlights.video.vertical import KILLFEED_H, KILLFEED_W, KILLFEED_X, KILLFEED_Y

FPS = 60  # analysis rate (the clip may be 120 fps)
WIDTH, HEIGHT = 96, 40
SPIKE_MIN = 4.0  # mean absolute change (0-255) a spike must reach ...
SPIKE_FACTOR = 4.0  # ... and this many times the clip's median change
SPIKE_GAP = 0.25  # seconds between two separate spikes
EARLIEST, LATEST = -1.0, 1.5  # a kill's row may appear this far before/after its nominal time
TOLERANCE = 0.1  # seconds two kills' shifts may differ and still agree
MIN_SHARE = 0.6  # share of a clip's kills that must agree


def feed_activity(ffmpeg_exe: Path, clip: Path) -> list[float]:
    """Per analysed frame, the mean absolute change of the kill-feed corner since the previous frame."""
    frames = gray_frames(ffmpeg_exe, clip, FPS, WIDTH, HEIGHT, (KILLFEED_X, KILLFEED_Y, KILLFEED_W, KILLFEED_H))
    activity = [0.0]
    for previous, current in zip(frames, frames[1:]):
        activity.append(sum(abs(a - b) for a, b in zip(previous, current)) / len(current))
    return activity


def spikes(activity: list[float], fps: float = FPS) -> list[float]:
    """Times (seconds) of sudden changes, strongest first within SPIKE_GAP."""
    if not activity:
        return []
    threshold = max(SPIKE_MIN, median(activity) * SPIKE_FACTOR)
    found: list[float] = []
    for i, value in enumerate(activity):
        if value < threshold:
            continue
        if (i > 0 and activity[i - 1] > value) or (i + 1 < len(activity) and activity[i + 1] > value):
            continue  # not a local peak
        t = i / fps
        if found and t - found[-1] < SPIKE_GAP:
            continue
        found.append(t)
    return found


def measure_offset(kill_times: list[float], spike_times: list[float]) -> tuple[float | None, int]:
    """(offset in seconds, kills matched): the shift most kills agree on, None when ambiguous."""
    if not kill_times or not spike_times:
        return None, 0
    candidates = sorted({round(s - k, 3) for k in kill_times for s in spike_times if EARLIEST <= s - k <= LATEST})
    if not candidates:
        return None, 0
    if len(kill_times) == 1 and len(candidates) > 1:
        return None, 0  # one kill, several possible rows: no way to tell

    def matches(shift: float) -> list[float]:
        found = []
        for k in kill_times:
            near = [s - k for s in spike_times if abs(s - k - shift) <= TOLERANCE]
            if near:
                found.append(min(near, key=lambda d: abs(d - shift)))
        return found

    best = max(candidates, key=lambda d: (len(matches(d)), -abs(d)))
    agreed = matches(best)
    if len(agreed) < max(1, ceil(len(kill_times) * MIN_SHARE)):
        return None, len(agreed)
    return round(median(agreed), 3), len(agreed)
