"""`--grenade-cam auto|all`: a generated camera riding just behind a grenade the POV player throws,
from the moment it leaves the hand until it goes off, holding briefly on the effect.

`auto` only follows grenades that matter (`grenade_worth`): an HE or fire that kills or hurts
several enemies, or a flash that blinds an enemy who dies soon after. Smokes and decoys are never
followed. The take is spliced into the POV clip like the other generated cams (it goes on top), so
the clip keeps its length and kill times. Camera offsets are estimates and there is no wall check:
a grenade bouncing off a wall can pull the camera into it (the review flags dark/flat frames).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from math import hypot, sqrt
from pathlib import Path

from highlights.demo.parser import DemoData
from highlights.models import Sequence
from highlights.recording.dyncams import POV_FOV, Pose, look_at, write_campath
from highlights.util.log import info

FOLLOW = 70.0  # camera distance behind the grenade, world units
RISE = 14.0  # ... and above it
LOOK_AHEAD = 40.0  # aim this far ahead of the grenade along its flight, so it sits low in frame
SMOOTH = 0.12  # seconds of flight averaged for the camera direction (softens bounces)
KEY_RATE = 32  # keyframes per second along the flight
HOLD = {"he": 0.7, "flash": 0.5, "molotov": 1.2}  # seconds held on the effect after detonation
MIN_FLIGHT = 0.3  # shorter flights (dropped grenades) are not worth a cut
FLASH_KILL_WINDOW = 3.0  # a blinded enemy killed within this many seconds makes a flash count
FIRE_WINDOW = 8.0  # fire damage/kills this long after a molotov lands count for it
HE_WINDOW = 0.5  # HE damage/kills this soon after detonation count for it
MIN_HURT = 2  # enemies an HE/fire must damage to count without a kill
MIN_BLIND = 1.0  # seconds of blindness that count
FIRE_WEAPONS = ("inferno", "molotov", "incgrenade")

Vec = tuple[float, float, float]
FlightLoader = Callable[[DemoData, dict], list[dict]]


@dataclass(frozen=True)
class GrenadePick:
    flight: dict
    worth: float
    reason: str


def _enemies(demo: DemoData, tick: int, thrower: str) -> set[str] | None:
    """Steamids on the other side in the round at `tick`; None when the roster is unknown."""
    round_index = max((r.index for r in demo.rounds if r.start_tick <= tick), default=0)
    roster = demo.rosters.get(round_index, {})
    side = roster.get(thrower)
    if not side:
        return None
    return {sid for sid, s in roster.items() if s != side}


DETONATION_KIND = {"he": "he", "flash": "flash", "molotov": "fire"}
FIRE_MATCH_TICKS = 16  # a fire may start this many ticks after its projectile's last sample


def detonate(flight: dict, demo: DemoData) -> dict | None:
    """The flight cut at its detonation (the projectile entity outlives it), with "burst" set to where
    it went off; None when no detonation matches (a dud, or a molotov that never caught fire)."""
    kind = DETONATION_KIND.get(flight["kind"])
    if kind is None:
        return None
    start, end = flight["start_tick"], flight["end_tick"]
    for event in demo.detonations:
        if event.kind != kind or event.thrower_steamid != flight["thrower"]:
            continue
        if not start < event.tick <= end + (FIRE_MATCH_TICKS if kind == "fire" else 2):
            continue
        if kind != "fire" and event.entity != flight["entity"]:
            continue
        points = [p for p in flight["points"] if p[0] <= event.tick]
        if len(points) < 2:
            return None
        return {**flight, "points": points, "end_tick": event.tick, "burst": event.pos}
    return None


def grenade_worth(flight: dict, demo: DemoData) -> GrenadePick | None:
    """Why a grenade deserves a grenade cam, or None."""
    kind, thrower, end = flight["kind"], flight["thrower"], flight["end_tick"]
    rate = demo.tickrate
    enemies = _enemies(demo, flight["start_tick"], thrower)

    def is_enemy(sid: str) -> bool:
        return enemies is None or sid in enemies

    if kind in ("he", "molotov"):
        window = HE_WINDOW if kind == "he" else FIRE_WINDOW
        weapons = ("hegrenade",) if kind == "he" else FIRE_WEAPONS
        last = end + int(window * rate)
        kills = [k for k in demo.kills if k.attacker_steamid == thrower and k.weapon in weapons and end - 2 <= k.tick <= last]
        hurt = {
            h.victim_steamid
            for h in demo.nade_hits
            if h.attacker_steamid == thrower and h.weapon in weapons and end - 2 <= h.tick <= last and is_enemy(h.victim_steamid)
        }
        name = "HE" if kind == "he" else "molotov"
        if kills:
            return GrenadePick(flight, 3.0 + len(kills), f"{name} kill" + (f" x{len(kills)}" if len(kills) > 1 else ""))
        if len(hurt) >= MIN_HURT:
            return GrenadePick(flight, 1.0 + 0.5 * len(hurt), f"{name} hits {len(hurt)}")
        return None
    if kind == "flash":
        blinded = [
            b for b in demo.blinds
            if b.attacker_steamid == thrower and b.entity == flight["entity"] and end - 2 <= b.tick <= end + 8
            and b.duration >= MIN_BLIND and is_enemy(b.victim_steamid)
        ]
        dying = {
            b.victim_steamid
            for b in blinded
            for k in demo.kills
            if k.victim_steamid == b.victim_steamid and b.tick <= k.tick <= b.tick + int(FLASH_KILL_WINDOW * rate)
        }
        if dying:
            return GrenadePick(flight, 2.0 + len(dying), "flash assist" + (f" x{len(dying)}" if len(dying) > 1 else ""))
    return None


def _position(points: list[tuple[int, Vec]], tick: float) -> Vec:
    if tick <= points[0][0]:
        return points[0][1]
    for (t0, p0), (t1, p1) in zip(points, points[1:]):
        if t0 <= tick <= t1:
            w = 0.0 if t1 == t0 else (tick - t0) / (t1 - t0)
            return tuple(a + (b - a) * w for a, b in zip(p0, p1))  # type: ignore[return-value]
    return points[-1][1]


def _direction(points: list[tuple[int, Vec]], tick: float, span: float, fallback: Vec) -> Vec:
    a, b = _position(points, tick - span), _position(points, tick + span)
    d = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    n = sqrt(d[0] ** 2 + d[1] ** 2 + d[2] ** 2)
    return fallback if n < 1.0 else (d[0] / n, d[1] / n, d[2] / n)


def follow_keyframes(flight: dict, clip_start_tick: int, tickrate: float) -> list[tuple[float, Pose]]:
    """Keyframes (clip seconds, pose) from release to detonation plus the hold."""
    points = flight["points"]
    start, end = flight["start_tick"], flight["end_tick"]
    span = SMOOTH * tickrate / 2
    first = _position(points, start + span * 2)
    origin = points[0][1]
    heading = (first[0] - origin[0], first[1] - origin[1], first[2] - origin[2])
    n = sqrt(sum(c * c for c in heading)) or 1.0
    direction: Vec = (heading[0] / n, heading[1] / n, heading[2] / n)
    keys: list[tuple[float, Pose]] = []
    steps = max(2, round((end - start) / tickrate * KEY_RATE))
    cam: Vec = origin
    for i in range(steps + 1):
        tick = start + (end - start) * i / steps
        direction = _direction(points, tick, span, direction)
        pos = _position(points, tick)
        flat = hypot(direction[0], direction[1]) or 1.0
        # Behind along the flight (horizontal part, so a steep lob does not put the camera under it).
        cam = (pos[0] - direction[0] / flat * FOLLOW, pos[1] - direction[1] / flat * FOLLOW, pos[2] + RISE)
        target = (pos[0] + direction[0] * LOOK_AHEAD, pos[1] + direction[1] * LOOK_AHEAD, pos[2] + direction[2] * LOOK_AHEAD)
        yaw, pitch = look_at(cam, target)
        keys.append(((tick - clip_start_tick) / tickrate, Pose(cam, yaw, pitch, POV_FOV)))
    # Hold where the grenade went off, looking at it.
    burst = flight.get("burst") or points[-1][1]
    yaw, pitch = look_at(cam, burst)
    hold_end = (end - clip_start_tick) / tickrate + HOLD.get(flight["kind"], 0.5)
    keys.append((hold_end, Pose(cam, yaw, pitch, POV_FOV)))
    keys = [(round(t, 4), pose) for t, pose in keys]
    if keys[0][0] > 0:
        keys.insert(0, (0.0, keys[0][1]))
    return keys


def _active_player(sequence: Sequence, tick: int) -> str | None:
    cameras = sorted(sequence.cameras, key=lambda c: c.tick)
    return next((c.steamid for c in reversed(cameras) if c.tick <= tick), None)


def plan_clip(sequence: Sequence, flights: list[dict], demo: DemoData, mode: str) -> list[GrenadePick]:
    """Grenades to follow in one POV clip: thrown by whoever the camera is on, landing inside the
    clip with room for the hold, best first and never overlapping."""
    rate = demo.tickrate
    picks: list[GrenadePick] = []
    for raw in flights:
        if raw["kind"] not in HOLD:
            continue  # smokes, decoys
        flight = detonate(raw, demo)
        if flight is None:
            continue
        if (flight["end_tick"] - flight["start_tick"]) / rate < MIN_FLIGHT:
            continue
        hold_end = flight["end_tick"] + int(HOLD[flight["kind"]] * rate)
        if flight["start_tick"] < sequence.start_tick or hold_end > sequence.end_tick:
            continue
        if _active_player(sequence, flight["start_tick"]) != flight["thrower"]:
            continue
        pick = grenade_worth(flight, demo)
        if pick is None and mode == "all":
            pick = GrenadePick(flight, 0.0, flight["kind"])
        if pick is not None:
            picks.append(pick)
    chosen: list[GrenadePick] = []
    for pick in sorted(picks, key=lambda p: -p.worth):
        a, b = pick.flight["start_tick"], pick.flight["end_tick"] + int(HOLD[pick.flight["kind"]] * rate)
        if all(b <= c.flight["start_tick"] or a >= c.flight["end_tick"] + int(HOLD[c.flight["kind"]] * rate) for c in chosen):
            chosen.append(pick)
    return sorted(chosen, key=lambda p: p.flight["start_tick"])


def add_grenade_sequences(
    sequences: list[Sequence],
    spatial_by_sequence: dict[int, dict],
    demo: DemoData,
    load_flights: FlightLoader,
    out_dir: Path,
    mode: str = "auto",
) -> tuple[list[Sequence], dict[int, dict]]:
    """Add one grenade-cam take per POV clip that has a grenade worth following (see module doc)."""
    from highlights.recording.dyn_sequences import insert_takes

    if mode == "off":
        return sequences, spatial_by_sequence
    povs = [s for s in sequences if s.cinematic_of is None and s.moments]
    wanted: dict[str, list[tuple[int, int]]] = {}
    for sequence in povs:
        for steamid in sequence.featured_steamids:
            wanted.setdefault(steamid, []).append((sequence.start_tick, sequence.end_tick))
    flights = load_flights(demo, wanted)
    rate = demo.tickrate
    new_for: dict[int, Sequence] = {}
    for sequence in povs:
        picks = plan_clip(sequence, flights, demo, mode)
        if not picks:
            continue
        keys: list[tuple[float, Pose]] = []
        segments = []
        for pick in picks:
            flight_keys = follow_keyframes(pick.flight, sequence.start_tick, rate)
            if keys:
                flight_keys = [k for k in flight_keys if k[0] > keys[-1][0]]  # drop the t=0 hold of later flights
            keys += flight_keys
            start = round((pick.flight["start_tick"] - sequence.start_tick) / rate, 2)
            end = round((pick.flight["end_tick"] - sequence.start_tick) / rate + HOLD[pick.flight["kind"]], 2)
            segments.append((start, end, pick))
        file = write_campath(out_dir / f"grenadecam_{sequence.start_tick}.campath", keys)
        info(f"{sequence.label}: grenade cam — " + ", ".join(f"{p.reason} at {a:.1f}s" for a, _, p in segments))
        new_for[sequence.number] = replace(
            sequence,
            label=f"{sequence.label} — grenade cam",
            campath=file,
            cinematic_of=sequence.number,
            splice_segments=[(a, b) for a, b, _ in segments],
            dynamic_cam=[
                {"start_seconds": a, "end_seconds": b, "kind": "grenade", "style": p.flight["kind"], "reason": p.reason}
                for a, b, p in segments
            ],
            cinematic_candidates=None,
            campath_retime=None,
            tilt=None,
        )
    if not new_for:
        return sequences, spatial_by_sequence
    return insert_takes(sequences, spatial_by_sequence, new_for)
