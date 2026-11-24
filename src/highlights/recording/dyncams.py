"""Generated cam paths that follow the POV player (--intro-cam, --downtime-cam).

Instead of a user-supplied cam path, the path is computed from the demo: a pose
relative to the player's eye (face, profile, gun, third-person, shoulder, helicopter) that
eases into the exact first-person eye pose at the end of an opener, or out of and
back into it during down time, so the cut back to the POV take has no jump.
Everything here is pure geometry on clip-second tracks; the demo is only read by
`spatial.sample_pov_tracks`. Offsets are estimates from reference clips and are
meant to be tuned (the constants below); nothing is checked against map geometry,
so a camera can end up inside a wall.

Angles are degrees, CS2 convention: positive yaw turns toward +y, positive pitch
looks down (same as `campaths.parse_campath`).
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from math import atan2, cos, degrees, hypot, radians, sin
from pathlib import Path

from highlights.recording.splice_plan import KILL_PAD, MIN_SEGMENT, _protected_windows

STYLES = ("face", "profile", "gun", "tps", "shoulder", "heli")

EYE_HEIGHT = 64.0  # standing eye height above the player's origin (feet)
POV_FOV = 90.0  # fov of the first-person view; assumed, not validated in-game
KEY_STEP = 1 / 16  # seconds between generated keyframes
# HLAE's default (cubic) interpolation only evaluates a path with >= 4 points (linear: 2);
# with fewer, `mirv_campath enabled 1` does nothing (advancedfx shared/AfxMath.h CanEval)
MIN_POINTS = 4
OPENER_SECONDS = 3.5  # opener length (shortened to stay clear of the first kill)
OPENER_HOLD = 0.55  # opener: fraction spent on the style shot before easing into the eye
DOWNTIME_MAX = 12.0  # longest stretch of one down-time pass
DOWNTIME_MIN = 2.5  # shorter gaps are left to the POV: fewer, fuller passes
MAX_DOWNTIME_PASSES = 2  # per clip (the longest gaps)
POV_GAP = 1.0  # plain POV kept between the opener and the first down-time cut
TRACK_SLACK = 0.25  # a track may end this much before the clip does; its last pose is held

Vec = tuple[float, float, float]
# (clip seconds, player origin x/y/z, yaw, pitch) of the camera player
Track = list[tuple[float, Vec, float, float]]


@dataclass(frozen=True)
class Pose:
    pos: Vec
    yaw: float
    pitch: float
    fov: float


@dataclass(frozen=True)
class Segment:
    start: float  # clip seconds
    end: float
    kind: str  # "intro" or "downtime"
    style: str = ""
    swoop: bool = True  # intro only: ease into the first-person view; False = hold the shot, hard cut


# ---- tracks ----------------------------------------------------------------


def _lerp(a: float, b: float, w: float) -> float:
    return a + (b - a) * w


def _lerp_angle(a: float, b: float, w: float) -> float:
    """Interpolate degrees along the shortest way round."""
    return a + ((b - a + 180.0) % 360.0 - 180.0) * w


def smoothstep(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def eye_pose_at(track: Track, t: float) -> Pose | None:
    """First-person pose (eye position, yaw, pitch) at clip time t, interpolated;
    None outside the track."""
    if not track or t < track[0][0] - 1e-9 or t > track[-1][0] + TRACK_SLACK:
        return None
    for (t0, p0, y0, a0), (t1, p1, y1, a1) in zip(track, track[1:]):
        if t0 <= t <= t1:
            w = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
            pos = tuple(_lerp(u, v, w) for u, v in zip(p0, p1))
            return Pose((pos[0], pos[1], pos[2] + EYE_HEIGHT), _lerp_angle(y0, y1, w) % 360.0, _lerp(a0, a1, w), POV_FOV)
    _, p, yaw, pitch = track[-1]
    return Pose((p[0], p[1], p[2] + EYE_HEIGHT), yaw, pitch, POV_FOV)


# ---- style shots -----------------------------------------------------------


def look_at(source: Vec, target: Vec) -> tuple[float, float]:
    """(yaw, pitch) that point a camera at `source` toward `target`."""
    dx, dy, dz = (target[0] - source[0], target[1] - source[1], target[2] - source[2])
    return degrees(atan2(dy, dx)) % 360.0, -degrees(atan2(dz, hypot(dx, dy)))


def _around(head: Vec, yaw: float, angle: float, radius: float, height: float) -> Vec:
    """Point `radius` away from the head, `angle` degrees round from the player's
    facing direction (0 = in front, -90 = their right, 180 = behind)."""
    a = radians(yaw + angle)
    return head[0] + radius * cos(a), head[1] + radius * sin(a), head[2] + height


@dataclass(frozen=True)
class Shot:
    """A camera placement relative to the player's head."""

    angle: float  # degrees round from the facing direction (0 front, -90 their right, +-180 behind)
    radius: float  # horizontal distance from the head
    height: float  # camera height above the head
    target_dz: float  # looks at the head plus this (ignored when aim == "player")
    fov: float
    aim: str = "look"  # "look" at the player, or "player": copy the player's own view angles


# Wide shots, 1-2 m from the player (reference clips: front and rear follow shots at
# roughly 150-200 units with a wide fov), drifting slowly while held.
def shot(style: str, u: float) -> Shot:
    if style == "face":  # tight close-up: a head is ~12 units tall, so this fills most of the frame
        # radius = how far the camera is from the head (units): smaller = tighter; slow push-in while held.
        # angle: front-left three-quarter (positive = the player's left). The rifle points forward on their
        # right, so a camera straight in front sits inside it. height/target_dz: a little above the head
        # centre, looking slightly down, so the chin and chest stay out of frame.
        return Shot(_lerp(36.0, 28.0, u), _lerp(26.0, 21.0, u), 8.0, 6.0, 70.0)
    if style == "profile":
        return Shot(_lerp(-95.0, -80.0, u), _lerp(70.0, 58.0, u), 2.0, 0.0, 85.0)
    if style == "gun":  # low front three-quarter on the weapon side, weapon and torso in frame
        return Shot(_lerp(-30.0, -85.0, u), _lerp(150.0, 115.0, u), _lerp(-36.0, -20.0, u), -20.0, 100.0)
    if style == "tps":  # over the shoulder, looking where the player looks
        return Shot(_lerp(-150.0, -158.0, u), _lerp(78.0, 70.0, u), _lerp(16.0, 22.0, u), 0.0, 85.0, "player")
    if style == "shoulder":  # broadcast over-the-shoulder: close behind and just right of the head, same view
        # ~35-40 units back and ~15 to their right (negative = right) fills the frame with the back of the head
        # and helmet, the weapon and the view ahead; slow push-in while held. Looks where the player looks.
        return Shot(_lerp(-157.0, -153.0, u), _lerp(40.0, 34.0, u), _lerp(7.0, 5.0, u), 0.0, 85.0, "player")
    if style == "heli":
        height = _lerp(760.0, 620.0, u)
        return Shot(-180.0, height * 0.4, height, 0.0, 90.0)
    raise ValueError(f"unknown cam style {style!r} (expected one of {', '.join(STYLES)})")


# Where every opener swoops to before the last push into the eye: just behind the head.
SWOOP_END = Shot(-180.0, 50.0, 6.0, 0.0, POV_FOV, "player")


def _shot_pose(sh: Shot, eye: Pose, aim_weight: float = 0.0) -> Pose:
    head = (eye.pos[0], eye.pos[1], eye.pos[2] - 4.0)
    pos = _around(head, eye.yaw, sh.angle, sh.radius, sh.height)
    yaw, pitch = look_at(pos, (head[0], head[1], head[2] + sh.target_dz))
    w = 1.0 if sh.aim == "player" else aim_weight
    return Pose(pos, _lerp_angle(yaw, eye.yaw, w) % 360.0, _lerp(pitch, eye.pitch, w), sh.fov)


def style_pose(style: str, eye: Pose, u: float) -> Pose:
    """The style's shot for the player at `eye`; u in [0, 1] is how far through the
    shot we are (slow drift while it is held)."""
    return _shot_pose(shot(style, u), eye)


def blend(a: Pose, b: Pose, w: float) -> Pose:
    """a at w=0, b at w=1."""
    return Pose(
        tuple(_lerp(u, v, w) for u, v in zip(a.pos, b.pos)),  # type: ignore[arg-type]
        _lerp_angle(a.yaw, b.yaw, w) % 360.0,
        _lerp(a.pitch, b.pitch, w),
        _lerp(a.fov, b.fov, w),
    )


def segment_pose(segment: Segment, eye: Pose, t: float) -> Pose:
    """Camera pose at clip time t inside the segment.

    Intro: holds the style shot, then spirals round to just behind the player's head
    and pushes into the exact first-person `eye` pose at the end (no jump on the cut to
    the POV); with `swoop` off it holds the shot to the end and hard-cuts to the POV like
    a down-time pass. Down time: one continuous pass of the style shot, hard-cut in and out."""
    u = (t - segment.start) / (segment.end - segment.start)
    u = min(1.0, max(0.0, u))
    if segment.kind != "intro" or not segment.swoop:
        return style_pose(segment.style, eye, u)
    if u <= OPENER_HOLD:
        return style_pose(segment.style, eye, u / OPENER_HOLD)
    s = (u - OPENER_HOLD) / (1 - OPENER_HOLD)
    sw = smoothstep(s)
    a, b = shot(segment.style, 1.0), SWOOP_END
    # Swing round the shorter way (a shot on the player's left ends behind them at +180, not -180).
    end_angle = b.angle + 360.0 if b.angle - a.angle < -180.0 else b.angle - 360.0 if b.angle - a.angle > 180.0 else b.angle
    mix = Shot(
        _lerp(a.angle, end_angle, sw),
        _lerp(a.radius, b.radius, sw),
        _lerp(a.height, b.height, sw),
        _lerp(a.target_dz, b.target_dz, sw),
        _lerp(a.fov, b.fov, sw),
        "look",
    )
    return blend(_shot_pose(mix, eye, sw), eye, smoothstep((s - 0.7) / 0.3))


# ---- planning --------------------------------------------------------------


def _subtract(start: float, end: float, taken: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """The parts of [start, end] not covered by the `taken` ranges."""
    free = [(start, end)]
    for a, b in sorted(taken):
        step: list[tuple[float, float]] = []
        for lo, hi in free:
            if b <= lo or a >= hi:
                step.append((lo, hi))
                continue
            if a > lo:
                step.append((lo, a))
            if b < hi:
                step.append((b, hi))
        free = step
    return free


def plan_segments(
    duration: float,
    kill_times: list[float],
    switch_times: list[float],
    intro: str | None,
    downtime: str | None,
    taken: list[tuple[float, float]] | None = None,
) -> list[Segment]:
    """Where generated cams replace the POV, in clip seconds.

    The opener runs from clip start until OPENER_SECONDS or one kill-padding before
    the first kill or camera switch, whichever is first (dropped when that leaves
    less than MIN_SEGMENT). Down-time passes use the gaps between the POV windows
    around the kills (same padding rule as the splice planner), split at camera
    switches so the player never changes mid-pass, minus the ranges `taken` by a real
    cam path; each is one full pass of at least DOWNTIME_MIN seconds (capped at
    DOWNTIME_MAX), and only the MAX_DOWNTIME_PASSES longest are kept so a clip is not
    chopped up. `intro`/`downtime` are the style names.
    """
    taken = taken or []
    blockers = sorted([*kill_times, *switch_times])
    segments: list[Segment] = []
    opener_end = min(OPENER_SECONDS, duration)
    if blockers:
        opener_end = min(opener_end, blockers[0] - KILL_PAD)
    opener_end = round(opener_end, 2)
    if intro and opener_end >= MIN_SEGMENT:
        segments.append(Segment(0.0, opener_end, "intro", intro))
    if downtime:
        windows = _protected_windows(kill_times)
        gaps = []
        cursor = opener_end + POV_GAP if segments else 0.0
        for a, b in windows:
            gaps.append((cursor, min(a, duration)))
            cursor = max(cursor, b)
        gaps.append((cursor, duration))
        passes: list[Segment] = []
        for lo, hi in gaps:
            cuts = [lo, *[s for s in sorted(switch_times) if lo < s < hi], hi]
            for a, b in zip(cuts, cuts[1:]):
                for fa, fb in _subtract(a, b, taken):
                    fb = min(fb, fa + DOWNTIME_MAX)
                    if fb - fa >= DOWNTIME_MIN:
                        passes.append(Segment(round(fa, 2), round(fb, 2), "downtime", downtime))
        longest = sorted(passes, key=lambda p: p.start - p.end)[:MAX_DOWNTIME_PASSES]
        segments.extend(longest)
    return sorted(segments, key=lambda s: s.start)


def pick_style(styles: list[str], seed: int) -> str:
    """One style from the list; deterministic per seed so a re-run picks the same."""
    return styles[0] if len(styles) == 1 else random.Random(seed).choice(styles)


def parse_styles(value: str) -> list[str]:
    """`face`, `face,tps` or `random` (every style) -> style names."""
    names = [part.strip().lower() for part in value.split(",") if part.strip()]
    if not names:
        raise ValueError("no cam style given")
    if names == ["random"]:
        return list(STYLES)
    unknown = [n for n in names if n not in STYLES]
    if unknown:
        raise ValueError(f"unknown cam style {unknown[0]!r} (expected one of {', '.join(STYLES)} or random)")
    return list(dict.fromkeys(names))


# ---- path files ------------------------------------------------------------


def _quaternion(yaw: float, pitch: float) -> tuple[float, float, float, float]:
    """(w, x, y, z) of yaw about z then pitch (positive = down), matching the
    quaternion convention `campaths._forward` reads."""
    a, b = radians(yaw) / 2, radians(pitch) / 2
    return cos(a) * cos(b), -sin(a) * sin(b), cos(a) * sin(b), sin(a) * cos(b)


def build_keyframes(segments: list[Segment], eye_at) -> list[tuple[float, Pose]]:
    """Keyframes (clip seconds, pose) over every segment. `eye_at(t)` gives the
    first-person pose. The path is rebased to start at the first recorded frame, so
    when the first segment starts later the camera holds its first pose from t=0."""
    keys: list[tuple[float, Pose]] = []
    for segment in segments:
        steps = max(2, round((segment.end - segment.start) / KEY_STEP))
        for i in range(steps + 1):
            t = segment.start + (segment.end - segment.start) * i / steps
            eye = eye_at(t)
            if eye is None:
                raise ValueError(f"no player position at {t:.2f}s for the {segment.kind} cam")
            keys.append((round(t, 4), segment_pose(segment, eye, t)))
    if keys and keys[0][0] > 0:
        keys.insert(0, (0.0, keys[0][1]))
    return keys


def write_campath(file: Path, keys: list[tuple[float, Pose]]) -> Path:
    """Write HLAE campath XML (quaternion and Euler angles, no roll)."""
    if len(keys) < MIN_POINTS:
        raise ValueError(f"a cam path needs at least {MIN_POINTS} points for HLAE to play it, got {len(keys)}")
    rows = []
    previous: tuple[float, float, float, float] | None = None
    for t, pose in keys:
        quat = _quaternion(pose.yaw, pose.pitch)
        # q and -q are the same rotation; keep neighbours on the same side so
        # interpolation never takes the long way round.
        if previous and sum(a * b for a, b in zip(quat, previous)) < 0:
            quat = tuple(-c for c in quat)  # type: ignore[assignment]
        previous = quat
        qw, qx, qy, qz = quat
        x, y, z = pose.pos
        rows.append(
            f'<p t="{t:.4f}" x="{x:.3f}" y="{y:.3f}" z="{z:.3f}" fov="{pose.fov:.2f}" '
            f'rx="0" ry="{pose.pitch:.4f}" rz="{pose.yaw:.4f}" '
            f'qw="{qw:.6f}" qx="{qx:.6f}" qy="{qy:.6f}" qz="{qz:.6f}"/>'
        )
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text("<campath><points>" + "".join(rows) + "</points></campath>", encoding="utf-8")
    return file
