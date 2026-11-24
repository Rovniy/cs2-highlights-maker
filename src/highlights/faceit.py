"""FACEIT match context (teams, opponent, ESEA division) for demos named after their match room.

FACEIT demo files are `<match id>-<n>-<m>.dem`, e.g. `1-06f966d6-...-18de41412123-1-1.dem`. The match
comes from the Data API (`GET /data/v4/matches/{id}`, server-side key in `[faceit] api_key` or the
`FACEIT_API_KEY` environment variable) and is cached in `~/.cs2-highlights/faceit/<id>.json` once it is
finished. ESEA leagues have no championship record in the API, so the division is read from the
competition name ("S59 NA Open1-8 East - Regular Season" -> "ESEA Open 1-8"); other competitions use
their name as the division ("BHOP Playoffs A").

The report's `match` block (read by the cotw overlay tool):
    {"match_id", "url", "league", "competition", "season", "region", "division", "group", "stage",
     "best_of", "teams": [A, B], "score": {A: n, B: n}, "winner",
     "players": {"<steamid64>": {"nickname", "team", "opponent"}}}
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path

import requests

from highlights.config import Config, app_data_dir
from highlights.demo.parser import DemoData
from highlights.util.log import warn

API = "https://open.faceit.com/data/v4"
MATCH_ID = re.compile(r"1-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
# "S59 NA Open1-8 East - Regular Season": season, region, division, bracket ("1-8"), group, stage
COMPETITION = re.compile(r"^S(\d+)\s+(\S+)\s+([A-Za-z]+)\s*(\d+-\d+)?\s*(.*?)(?:\s+-\s+(.+))?$")


def match_id_of(*names: str) -> str | None:
    """The FACEIT match id in the first name that has one (file name, then batch label)."""
    for name in names:
        found = MATCH_ID.search(name or "")
        if found:
            return found.group(0).lower()
    return None


def api_key(config: Config) -> str:
    return os.environ.get("FACEIT_API_KEY") or config.faceit.api_key


def enabled(config: Config, paths: list[Path]) -> bool:
    """Whether to look matches up; warns once when FACEIT demos are given without an API key."""
    if api_key(config):
        return True
    if any(match_id_of(p.name) for p in paths):
        warn("FACEIT demos but no API key (\\[faceit] api_key or FACEIT_API_KEY): no team/opponent/division")
    return False


def _get(path: str, key: str) -> dict:
    response = requests.get(f"{API}{path}", headers={"Authorization": f"Bearer {key}"}, timeout=20)
    response.raise_for_status()
    return response.json()


def fetch_match(match_id: str, key: str, cache_dir: Path | None = None) -> dict:
    """The API match object plus `organizer_name`; finished matches are cached and never fetched again."""
    cache = (cache_dir or app_data_dir() / "faceit") / f"{match_id}.json"
    if cache.is_file():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except ValueError:
            pass
    match = _get(f"/matches/{match_id}", key)
    if match.get("organizer_id"):
        try:
            match["organizer_name"] = _get(f"/organizers/{match['organizer_id']}", key).get("name", "")
        except requests.RequestException:
            pass
    if match.get("status") == "FINISHED":
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(match, indent=2, ensure_ascii=False), encoding="utf-8")
    return match


def parse_competition(name: str) -> dict:
    found = COMPETITION.match(name.strip())
    if not found:
        return {}
    season, region, division, bracket, group, stage = found.groups()
    # "Open 1-8" and "Open 9-10" are different divisions, so the bracket belongs to it
    division = f"{division} {bracket}" if bracket else division
    return {"season": int(season), "region": region, "division": division, "group": group, "stage": stage or ""}


def _side_teams(demo: DemoData, team_of: dict[str, str]) -> dict[tuple[int, str], str]:
    """(round, side) -> FACEIT team, by majority of the rostered players on that side."""
    out = {}
    for round_index, roster in demo.rosters.items():
        votes: dict[str, Counter] = {}
        for steamid, side in roster.items():
            if steamid in team_of:
                votes.setdefault(side, Counter())[team_of[steamid]] += 1
        for side, counter in votes.items():
            out[(round_index, side)] = counter.most_common(1)[0][0]
    return out


def match_block(match: dict, demo: DemoData) -> dict:
    factions = [match.get("teams", {}).get(f, {}) for f in ("faction1", "faction2")]
    teams = [f.get("name", "") for f in factions]
    players: dict[str, dict] = {}
    for faction, team, opponent in zip(factions, teams, reversed(teams)):
        for p in faction.get("roster", []):
            if p.get("game_player_id"):
                players[str(p["game_player_id"])] = {"nickname": p.get("nickname", ""), "team": team, "opponent": opponent}
    # Stand-ins who played but are not on the FACEIT roster: the team their demo side belongs to.
    sides = _side_teams(demo, {s: p["team"] for s, p in players.items()})
    for round_index in sorted(demo.rosters):
        for steamid, side in demo.rosters[round_index].items():
            team = sides.get((round_index, side))
            if steamid not in players and team:
                opponent = teams[1 - teams.index(team)]
                players[steamid] = {"nickname": demo.player_names.get(steamid, ""), "team": team, "opponent": opponent}
    score = (match.get("results") or {}).get("score") or {}
    winner = (match.get("results") or {}).get("winner")
    competition = match.get("competition_name", "")
    league = match.get("organizer_name", "")
    parsed = parse_competition(competition)
    # "ESEA Main" for ESEA league matches, otherwise the competition name ("BHOP Playoffs A")
    division = f"{league} {parsed['division']}".strip() if parsed else competition
    return {
        "match_id": match.get("match_id", ""),
        "url": match.get("faceit_url", "").replace("{lang}", "en"),
        "league": league,
        "competition": competition,
        **parsed,
        "division": division,
        "best_of": match.get("best_of"),
        "teams": teams,
        "score": {team: score.get(f) for team, f in zip(teams, ("faction1", "faction2")) if f in score},
        "winner": teams[0] if winner == "faction1" else teams[1] if winner == "faction2" else None,
        "players": players,
    }


def lookup(demo: DemoData, config: Config, *names: str) -> dict | None:
    """The `match` block for a FACEIT demo, or None (not a FACEIT file name, no API key, API error)."""
    match_id = match_id_of(demo.path.name, *names)
    if match_id is None:
        return None
    key = api_key(config)
    if not key:
        warn("FACEIT demo but no API key (\\[faceit] api_key or FACEIT_API_KEY): no team/opponent/division in the report")
        return None
    try:
        return match_block(fetch_match(match_id, key), demo)
    except (requests.RequestException, ValueError) as exc:
        warn(f"FACEIT lookup for {match_id} failed, no team/opponent/division in the report: {exc}")
        return None


def add_to_report(report_path: Path, block: dict | None) -> None:
    if block is None:
        return
    data = json.loads(report_path.read_text(encoding="utf-8"))
    data["match"] = block
    report_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
