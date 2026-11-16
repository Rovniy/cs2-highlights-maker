"""`--weapon` / `--headshot-only` / `--per-kill`: keep only some kills, optionally one clip per kill.

Weapon names are the demo's `player_death` names ("deagle", "awp", "ak47", "m4a1_silencer", ...);
ALIASES maps the common short names onto them.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field

from highlights.analysis.scoring import score_moments
from highlights.demo.parser import DemoData
from highlights.models import Kill, Moment

ALIASES: dict[str, tuple[str, ...]] = {
    "deag": ("deagle",),
    "desert_eagle": ("deagle",),
    "ak": ("ak47",),
    "ak-47": ("ak47",),
    "m4": ("m4a1", "m4a1_silencer"),
    "m4a4": ("m4a1",),
    "m4a1s": ("m4a1_silencer",),
    "m4a1-s": ("m4a1_silencer",),
    "usp": ("usp_silencer",),
    "usp-s": ("usp_silencer",),
    "p2000": ("hkp2000",),
    "scout": ("ssg08",),
    "r8": ("revolver",),
    "zeus": ("taser",),
    "auto": ("g3sg1", "scar20"),
}

# --per-kill clips: just enough to see the kill coming and land it (the moment defaults are 4 s / 3 s).
PER_KILL_LEAD_IN = 2.0
PER_KILL_LEAD_OUT = 1.5


def parse_weapons(values: Collection[str]) -> set[str]:
    """`--weapon` values ("deagle", "awp,ssg08", "weapon_ak47"; repeatable) as demo weapon names."""
    names: set[str] = set()
    for value in values:
        for part in (p.strip().lower().replace(" ", "_") for p in value.split(",")):
            if not part:
                continue
            part = part.removeprefix("weapon_")
            names.update(ALIASES.get(part, (part,)))
    return names


@dataclass
class KillFilter:
    weapons: set[str] = field(default_factory=set)  # empty = any weapon
    headshot_only: bool = False
    per_kill: bool = False

    @property
    def active(self) -> bool:
        return bool(self.weapons) or self.headshot_only or self.per_kill

    def matches(self, kill: Kill) -> bool:
        if self.headshot_only and not kill.headshot:
            return False
        if not self.weapons:
            return True
        return kill.weapon.lower() in self.weapons or ("knife" in self.weapons and kill.is_knife)

    def describe(self) -> str:
        parts = [", ".join(sorted(self.weapons)) if self.weapons else "any weapon"]
        if self.headshot_only:
            parts.append("headshots only")
        if self.per_kill:
            parts.append("one clip per kill")
        return "; ".join(parts)


def apply_kill_filter(moments: list[Moment], demo: DemoData, kill_filter: KillFilter) -> list[Moment]:
    """The moments rebuilt from their matching kills, rescored.

    Without `per_kill` a moment keeps only its matching kills (and its clutch) and is dropped when
    none match; with `per_kill` every matching kill becomes its own single-kill moment (no clutch)."""
    if not kill_filter.active:
        return moments
    result: list[Moment] = []
    for moment in moments:
        kills = [k for k in moment.kills if kill_filter.matches(k)]
        if not kills:
            continue
        if kill_filter.per_kill:
            result.extend(
                Moment(steamid=moment.steamid, name=moment.name, side=moment.side, round_index=moment.round_index, kills=[k])
                for k in kills
            )
        else:
            result.append(
                Moment(
                    steamid=moment.steamid,
                    name=moment.name,
                    side=moment.side,
                    round_index=moment.round_index,
                    kills=kills,
                    clutch=moment.clutch,
                )
            )
    score_moments(result, demo)
    result.sort(key=lambda m: m.first_tick)
    return result


def weapons_seen(demos: Collection[DemoData]) -> list[str]:
    """Every weapon name in these demos' kills, most used first (for the "nothing matched" hint)."""
    counts: dict[str, int] = {}
    for demo in demos:
        for kill in demo.kills:
            counts[kill.weapon] = counts.get(kill.weapon, 0) + 1
    return sorted(counts, key=lambda w: (-counts[w], w))
