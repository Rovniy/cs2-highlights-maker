"""Heuristic "watchability" score for a Moment. All knobs live in WEIGHTS."""

from __future__ import annotations

from highlights.demo.parser import DemoData
from highlights.models import Moment

WEIGHTS = {
    "kill": 1.0,
    "multikill": {2: 0.5, 3: 2.0, 4: 4.0, 5: 8.0},
    "headshot": 0.2,
    "knife": 3.0,
    "zeus": 3.0,
    "awp_noscope": 2.5,
    "thrusmoke": 1.5,
    "attackerblind": 2.0,
    "wallbang": 1.0,
    "clutch_won_base": 2.0,
    "clutch_won_per_opponent": 1.5,
    "clutch_lost_with_kills": 1.0,
    "pistol_round_multiplier": 1.15,
    "overtime_multiplier": 1.2,
    "final_round_multiplier": 1.3,
    "lost_round_multiplier": 0.85,
    "match_point_multiplier": 1.2,  # either team one round from winning the map
    "eco_multiplier": 1.3,  # the player's team was on an eco against a buy
    "anti_eco_multiplier": 0.85,  # the player's team had a buy against an eco
}

ECO_MAX = 1500  # team average equipment value at freeze end at or below this = eco
BUY_MIN = 3500  # ... at or above this = full buy

# MR12: rounds 0 and 12 are pistol rounds, overtime starts at round 24.
PISTOL_ROUND_INDEXES = (0, 12)
OVERTIME_START_INDEX = 24


def _team_of(roster: dict[str, str], team_a: set[str]) -> dict[str, str]:
    """Side ("CT"/"T") of team A and team B in a round, from who of team A plays where."""
    a_sides = [side for sid, side in roster.items() if sid in team_a]
    if not a_sides:
        return {}
    a_side = max(("CT", "T"), key=a_sides.count)
    return {"A": a_side, "B": "T" if a_side == "CT" else "CT"}


def wins_needed(rounds_played: int) -> int:
    """Rounds a team needs to win the map: 13 in regulation (MR12), then MR3 overtimes (16, 19, ...)."""
    if rounds_played < 24:
        return 13
    return 13 + 3 * ((rounds_played - 24) // 6 + 1)


def round_context(demo: DemoData) -> dict[int, dict]:
    """Per round: score before it, whether it is a match point, and each side's economy."""
    first = next((demo.rosters[r.index] for r in demo.rounds if demo.rosters.get(r.index)), None)
    team_a = {sid for sid, side in (first or {}).items() if side == "CT"}
    wins = {"A": 0, "B": 0}
    context: dict[int, dict] = {}
    for round_ in demo.rounds:
        sides = _team_of(demo.rosters.get(round_.index, {}), team_a)
        played = wins["A"] + wins["B"]
        economy: dict[str, float] = {}
        roster = demo.rosters.get(round_.index, {})
        for side in ("CT", "T"):
            values = [v for sid, v in demo.equipment.get(round_.index, {}).items() if roster.get(sid) == side]
            if values:
                economy[side] = sum(values) / len(values)
        context[round_.index] = {
            "match_point": bool(sides) and max(wins.values()) == wins_needed(played) - 1,
            "economy": economy,
        }
        if sides:
            for team, side in sides.items():
                if round_.winner == side:
                    wins[team] += 1
    return context


def context_tags(moment: Moment, context: dict | None) -> list[str]:
    """Round context worth a score bonus or penalty, for display."""
    if not context:
        return []
    tags = []
    if context.get("match_point"):
        tags.append("match point")
    economy = context.get("economy", {})
    other = "T" if moment.side == "CT" else "CT"
    if moment.side in economy and other in economy:
        if economy[moment.side] <= ECO_MAX and economy[other] >= BUY_MIN:
            tags.append("eco")
        elif economy[moment.side] >= BUY_MIN and economy[other] <= ECO_MAX:
            tags.append("anti-eco")
    return tags


def score_moment(moment: Moment, demo: DemoData, context: dict | None = None) -> float:
    score = 0.0
    kill_count = moment.kill_count
    score += kill_count * WEIGHTS["kill"]
    multikill = WEIGHTS["multikill"]
    score += multikill.get(min(kill_count, 5), multikill[5] if kill_count > 5 else 0.0)

    for kill in moment.kills:
        if kill.headshot:
            score += WEIGHTS["headshot"]
        if kill.is_knife:
            score += WEIGHTS["knife"]
        if kill.is_zeus:
            score += WEIGHTS["zeus"]
        if kill.is_awp_noscope:
            score += WEIGHTS["awp_noscope"]
        if kill.thrusmoke:
            score += WEIGHTS["thrusmoke"]
        if kill.attackerblind:
            score += WEIGHTS["attackerblind"]
        if kill.penetrated > 0:
            score += WEIGHTS["wallbang"]

    if moment.clutch is not None:
        if moment.clutch.won:
            score += WEIGHTS["clutch_won_base"] + WEIGHTS["clutch_won_per_opponent"] * moment.clutch.opponents
        elif moment.clutch.kills >= 2:
            score += WEIGHTS["clutch_lost_with_kills"]

    if moment.round_index in PISTOL_ROUND_INDEXES:
        score *= WEIGHTS["pistol_round_multiplier"]
    if moment.round_index >= OVERTIME_START_INDEX:
        score *= WEIGHTS["overtime_multiplier"]
    if moment.round_index == len(demo.rounds) - 1:
        score *= WEIGHTS["final_round_multiplier"]

    tags = context_tags(moment, context)
    if "match point" in tags:
        score *= WEIGHTS["match_point_multiplier"]
    if "eco" in tags:
        score *= WEIGHTS["eco_multiplier"]
    if "anti-eco" in tags:
        score *= WEIGHTS["anti_eco_multiplier"]

    round_ = demo.round_at(moment.round_index)
    if round_ is not None and moment.side and round_.winner and round_.winner != moment.side:
        score *= WEIGHTS["lost_round_multiplier"]

    return round(score, 3)


def score_moments(moments: list[Moment], demo: DemoData) -> None:
    context = round_context(demo)
    for moment in moments:
        moment.score = score_moment(moment, demo, context.get(moment.round_index))
        moment.tags = context_tags(moment, context.get(moment.round_index))
