"""Decide which kills, if any, deserve a slow-motion beat (--slow-mode auto).

Decided from data only, over the whole pool of moments at once, so it can be previewed
(`top`, `--json-only`) before anything is recorded. Slow-motion is a post-process on the
finished clip (video/slowmo.py): the game timeline, kill times in the clip and every other
plan are untouched; only the report's video times are remapped afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil

from dataclasses import replace

from highlights.models import Kill, Moment, Sequence

SPEED = 0.4  # playback speed inside the window
LEAD = 0.7  # seconds of clip before the kill that are slowed
TAIL = 1.1  # seconds of clip after the kill that are slowed
MIN_WORTH = 2.5  # below this a kill never gets slow-motion (noscope, knife, zeus, 4K/ace or clutch finish qualify)
MAX_FRACTION = 0.34  # at most this share of the clips (rounded up) gets one, so it stays special
KILL_WORTH = {"knife": 3.0, "zeus": 3.0, "noscope": 2.5, "blind": 2.0, "smoke": 1.5, "wallbang": 1.0, "headshot": 0.2}
FINISHER_WORTH = {4: 3.0, 5: 6.0}  # the last kill of a 4K / ACE
CLUTCH_FINISH_WORTH = 3.0  # the last kill of a won clutch


@dataclass(frozen=True)
class SlowmoPick:
    kill: Kill
    worth: float
    reason: str


def _kill_worth(kill: Kill) -> tuple[float, list[str]]:
    flags = {
        "knife": kill.is_knife,
        "zeus": kill.is_zeus,
        "noscope": kill.is_awp_noscope,
        "blind": kill.attackerblind,
        "smoke": kill.thrusmoke,
        "wallbang": kill.penetrated > 0,
        "headshot": kill.headshot,
    }
    names = [name for name, on in flags.items() if on]
    return sum(KILL_WORTH[name] for name in names), names


def best_kill(moment: Moment) -> SlowmoPick | None:
    """The kill of a moment most worth a slow-motion beat (the later one on ties), or None."""
    best: SlowmoPick | None = None
    kills = sorted(moment.kills, key=lambda k: k.tick)
    for index, kill in enumerate(kills):
        worth, names = _kill_worth(kill)
        last = index == len(kills) - 1
        if last and moment.kill_count >= 4:
            worth += FINISHER_WORTH[min(moment.kill_count, 5)]
            names.append("ace finish" if moment.kill_count >= 5 else "4K finish")
        if last and moment.clutch is not None and moment.clutch.won:
            worth += CLUTCH_FINISH_WORTH
            names.append(f"1v{moment.clutch.opponents} clutch finish")
        if worth >= MIN_WORTH and (best is None or worth >= best.worth):
            best = SlowmoPick(kill, round(worth, 2), ", ".join(names))
    return best


def choose(picks: list[SlowmoPick | None]) -> list[SlowmoPick | None]:
    """Keep only the best picks (at most MAX_FRACTION of the clips, rounded up)."""
    allowed = ceil(len(picks) * MAX_FRACTION)
    ranked = sorted((i for i, pick in enumerate(picks) if pick), key=lambda i: -picks[i].worth)  # type: ignore[union-attr]
    keep = set(ranked[:allowed])
    return [pick if i in keep else None for i, pick in enumerate(picks)]


def window(kill_seconds: float, duration: float) -> tuple[float, float]:
    """The slowed stretch (clip seconds) around a kill, clamped to the clip."""
    return round(max(0.0, kill_seconds - LEAD), 2), round(min(duration, kill_seconds + TAIL), 2)


def extra_seconds(start: float, end: float, speed: float = SPEED) -> float:
    """How much longer the clip gets."""
    return (end - start) * (1 / speed - 1)


def remap(t: float, start: float, end: float, speed: float = SPEED) -> float:
    """A clip time before slow-motion -> its time after."""
    if t <= start:
        return t
    if t >= end:
        return t + extra_seconds(start, end, speed)
    return start + (t - start) / speed


def plan_sequences(sequences: list[Sequence], tickrate: float, max_duration: float = 0.0) -> list[Sequence]:
    """Give the best POV clips a slow-motion beat (`Sequence.slowmo`); the rest stay as they are.

    One beat per clip, on its best kill. Only POV sequences take part (cinematic takes are cut
    into their POV clip). When `max_duration` is set the added seconds must fit in it: the
    lowest-worth beats are dropped first."""
    pov = [s for s in sequences if s.cinematic_of is None]
    picks = []
    for sequence in pov:
        found = [p for p in (best_kill(m) for m in sequence.moments) if p]
        picks.append(max(found, key=lambda p: p.worth) if found else None)
    chosen = choose(picks)
    plan: dict[int, dict] = {}
    for sequence, pick in zip(pov, chosen):
        if pick is None:
            continue
        duration = sequence.duration_seconds(tickrate)
        start, end = window((pick.kill.tick - sequence.start_tick) / tickrate, duration)
        plan[sequence.number] = {
            "kill_tick": pick.kill.tick,
            "start_seconds": start,
            "end_seconds": end,
            "speed": SPEED,
            "worth": pick.worth,
            "reason": pick.reason,
            "victim": pick.kill.victim_name,
        }
    if max_duration > 0:
        total = sum(s.duration_seconds(tickrate) for s in pov)
        while plan and total + sum(extra_seconds(v["start_seconds"], v["end_seconds"]) for v in plan.values()) > max_duration:
            del plan[min(plan, key=lambda n: plan[n]["worth"])]
    return [replace(s, slowmo=plan.get(s.number)) if s.number in plan else s for s in sequences]
