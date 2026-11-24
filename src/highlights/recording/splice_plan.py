"""Plan when a cinematic take can replace the POV footage of the same play.

Both takes cover the same ticks, so they line up frame for frame; the cinematic
is cut in during down time between kills, only while its camera is pointed at
the player. Visibility is a view-cone test (no map geometry, so walls between the
camera and the player are not detected) and the whole plan is computed from the
demo and the cam path, before anything is recorded.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Callable
from math import atan, atan2, degrees, dist, radians, tan

from highlights.floors import Floors
from highlights.recording.campaths import CamPath, pose_at

SAMPLE_STEP = 0.1  # seconds between visibility checks
KILL_PAD = 1.0  # POV is kept this long before and after a kill ...
KILL_PAD_FLOOR = 0.4  # ... but never less than this, even between close kills
MIN_SEGMENT = 1.5  # shorter cinematic cuts are dropped
MERGE_GAP = 0.5  # POV blips shorter than this inside a cinematic cut are absorbed
VIEW_MARGIN = 0.85  # fraction of the half-FOV the player must sit inside
ASPECT = 16 / 9
MAX_DISTANCE = 4000.0  # world units; farther than this the player is a speck
CAM_BELOW_TOLERANCE = 30.0  # camera farther below the player than this is under the floor (player z is the feet)

Point = tuple[float, float, float]
Trajectory = list[tuple[float, Point]]  # (seconds from clip start, position), ascending


def position_at(samples: Trajectory, t: float) -> Point | None:
    if not samples or t < samples[0][0] or t > samples[-1][0]:
        return None
    index = bisect_right([s[0] for s in samples], t) - 1
    if index >= len(samples) - 1:
        return samples[-1][1]
    (t0, a), (t1, b) = samples[index], samples[index + 1]
    w = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
    return tuple(x + (y - x) * w for x, y in zip(a, b))  # type: ignore[return-value]


def _in_view(
    position: Point, forward: Point, fov: float, target: Point, margin: float = VIEW_MARGIN, floors: Floors | None = None
) -> bool:
    d = (target[0] - position[0], target[1] - position[1], target[2] - position[2])
    distance = dist(position, target)
    if not 50 < distance <= MAX_DISTANCE:
        return False
    if position[2] < target[2] - CAM_BELOW_TOLERANCE:
        return False  # camera under the player's floor
    # Camera basis without roll: left = up_world x forward, up = forward x left.
    left = (-forward[1], forward[0], 0.0)
    norm = (left[0] ** 2 + left[1] ** 2) ** 0.5
    if norm < 1e-6:  # looking straight up/down
        return False
    left = (left[0] / norm, left[1] / norm, 0.0)
    up = (
        forward[1] * left[2] - forward[2] * left[1],
        forward[2] * left[0] - forward[0] * left[2],
        forward[0] * left[1] - forward[1] * left[0],
    )
    dot = lambda u, v: u[0] * v[0] + u[1] * v[1] + u[2] * v[2]  # noqa: E731
    ahead = dot(d, forward)
    if ahead <= 0:
        return False
    half_h = radians(fov) / 2
    half_v = atan(tan(half_h) / ASPECT)  # fov is the horizontal angle
    yaw_off = abs(atan2(dot(d, left), ahead))
    pitch_off = abs(atan2(dot(d, up), ahead))
    if yaw_off > half_h * margin or pitch_off > half_v * margin:
        return False
    return floors is None or not floors.hides(position, target)  # a level below the camera's


def _protected_windows(kill_times: list[float]) -> list[tuple[float, float]]:
    """POV windows around the kills. The padding between two kills shrinks so a
    minimum-length cut still fits in the gap when they are only a bit too close for
    the full padding; kills too close for any cut simply merge into one window."""
    kills = sorted(kill_times)

    def pad(gap: float | None) -> float:
        if gap is None:
            return KILL_PAD
        return min(KILL_PAD, max(KILL_PAD_FLOOR, (gap - MIN_SEGMENT) / 2))

    windows = []
    for i, kill in enumerate(kills):
        before = pad(kill - kills[i - 1] if i > 0 else None)
        after = pad(kills[i + 1] - kill if i + 1 < len(kills) else None)
        windows.append((kill - before, kill + after))
    return windows


def plan_splice(
    duration: float,
    path: CamPath,
    kill_times: list[float],
    target_at: Callable[[float], Point | None],
    floors: Floors | None = None,
) -> list[tuple[float, float]]:
    """Segments (start, end) in seconds from clip start where the cinematic replaces the POV.

    kill_times are the POV player's kills (clip seconds); target_at gives the
    player's position at a time (None when unknown, which counts as not visible).
    """
    protected = _protected_windows(kill_times)
    steps = int(duration / SAMPLE_STEP) + 1
    visible: list[bool] = []
    for i in range(steps):
        t = i * SAMPLE_STEP
        pose = pose_at(path, t)
        target = target_at(t)
        ok = (
            pose is not None
            and target is not None
            and not any(a <= t <= b for a, b in protected)
            and _in_view(pose[0], pose[1], pose[2], target, floors=floors)
        )
        visible.append(ok)

    runs: list[list[float]] = []
    for i, ok in enumerate(visible):
        if not ok:
            continue
        t = i * SAMPLE_STEP
        if runs and t - runs[-1][1] <= MERGE_GAP + SAMPLE_STEP + 1e-9:
            runs[-1][1] = t
        else:
            runs.append([t, t])
    # A run of one sample spans one step of footage.
    return [
        (round(a, 2), round(min(b + SAMPLE_STEP, duration), 2))
        for a, b in runs
        if b + SAMPLE_STEP - a >= MIN_SEGMENT - 1e-9
    ]


def coverage(segments: list[tuple[float, float]]) -> float:
    return sum(b - a for a, b in segments)
