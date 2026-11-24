"""Which kills earn an x-ray shot, and when to dissolve it in.

A kill qualifies when the bullet went through a wall (`penetrated` surfaces beyond the players
standing in its way) or through smoke. Demos count other players in `penetrated` as well as walls (not
verified: see the `check-ingame` notes), so with positions we subtract the players on the shot's line;
a kill that only passed through another player is not a wallbang here. Without positions the raw
`penetrated` count is trusted. Geometry constants are estimates, not tuned on real demos.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from math import dist

from highlights.demo.parser import DemoData
from highlights.models import Kill, Sequence

EYE_HEIGHT = 64.0  # shooter's eye above the feet position in the demo
CHEST_HEIGHT = 48.0  # aim point on the victim
BODY_HEIGHT = 36.0  # centre of a bystander's body
PLAYER_RADIUS = 40.0  # a bystander this close to the shot line is "in the way"
LINE_MARGIN = 0.05  # ignore bystanders within this fraction of the line's ends (the shooter and victim themselves)

LEAD = 1.0  # seconds of x-ray before a qualifying kill ...
TAIL = 0.5  # ... and after it, at full strength
FADE = 0.4  # dissolve in and out, outside that span

Point = tuple[float, float, float]
Players = list[tuple[str, Point, bool]]  # (steamid, position, alive) at one tick
Sampler = Callable[[DemoData, list[int]], dict[int, Players]]


def kill_key(kill: Kill) -> tuple[int, str, str]:
    return kill.tick, kill.attacker_steamid, kill.victim_steamid


def blockers_on_line(kill: Kill, players: Players) -> int:
    """How many other living players stand on the line from the shooter's eye to the victim's chest."""
    positions = {sid: pos for sid, pos, _ in players}
    shooter, victim = positions.get(kill.attacker_steamid), positions.get(kill.victim_steamid)
    if shooter is None or victim is None:
        return 0
    a = (shooter[0], shooter[1], shooter[2] + EYE_HEIGHT)
    b = (victim[0], victim[1], victim[2] + CHEST_HEIGHT)
    d = tuple(y - x for x, y in zip(a, b))
    length_sq = sum(c * c for c in d)
    if length_sq < 1e-6:
        return 0
    count = 0
    for sid, pos, alive in players:
        if sid in (kill.attacker_steamid, kill.victim_steamid) or not alive:
            continue
        centre = (pos[0], pos[1], pos[2] + BODY_HEIGHT)
        t = sum((c - x) * k for c, x, k in zip(centre, a, d)) / length_sq
        if not LINE_MARGIN <= t <= 1 - LINE_MARGIN:
            continue
        nearest = tuple(x + k * t for x, k in zip(a, d))
        if dist(centre, nearest) <= PLAYER_RADIUS:
            count += 1
    return count


def xray_reasons(kill: Kill, blockers: int = 0) -> tuple[str, ...]:
    reasons = []
    if kill.penetrated - blockers > 0:
        reasons.append("wallbang")
    if kill.thrusmoke:
        reasons.append("through smoke")
    return tuple(reasons)


def classify_kills(
    sequences: list[Sequence], demo: DemoData, sample: Sampler
) -> dict[tuple[int, str, str], tuple[str, ...]]:
    """Reasons per selected kill (only kills that have one). One position query covers every
    penetrating kill; where it has no data the raw `penetrated` count stands."""
    kills = {kill_key(k): k for s in sequences for m in s.moments for k in m.kills}
    wanted = sorted({k.tick - 1 for k in kills.values() if k.penetrated > 0})
    snapshots = sample(demo, wanted) if wanted else {}
    result = {}
    for key, kill in kills.items():
        players = snapshots.get(kill.tick - 1)
        reasons = xray_reasons(kill, blockers_on_line(kill, players) if players else 0)
        if reasons:
            result[key] = reasons
    return result


@dataclass(frozen=True)
class Window:
    start: float  # full strength from here ...
    end: float  # ... to here (clip seconds)
    fade_in: float
    fade_out: float
    kills: tuple[int, ...] = ()  # kill ticks inside it
    reasons: tuple[str, ...] = ()


def plan_windows(
    kills: list[tuple[float, int, tuple[str, ...]]],
    duration: float,
    lead: float = LEAD,
    tail: float = TAIL,
    fade: float = FADE,
) -> list[Window]:
    """Full-strength spans around each (clip seconds, tick, reasons) kill, ramps outside them.

    Ramps shrink at the clip's edges (no room to fade in before 0 s), and two kills whose ramps
    would overlap share one window so the x-ray never flickers off between them."""
    spans: list[list] = []
    for seconds, tick, reasons in sorted(kills):
        start, end = max(0.0, seconds - lead), min(duration, seconds + tail)
        if spans and start - spans[-1][1] < 2 * fade:
            spans[-1][1] = max(spans[-1][1], end)
            spans[-1][2].append(tick)
            spans[-1][3].extend(r for r in reasons if r not in spans[-1][3])
        else:
            spans.append([start, end, [tick], list(reasons)])
    return [
        Window(
            round(start, 3),
            round(end, 3),
            round(min(fade, start), 3),
            round(min(fade, duration - end), 3),
            tuple(ticks),
            tuple(reasons),
        )
        for start, end, ticks, reasons in spans
    ]
