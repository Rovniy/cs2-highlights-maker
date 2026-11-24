"""`highlights scan DIR`: which demos in a folder hold a play worth keeping for some players."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from highlights.models import Moment

KEEP, REMOVE, UNREADABLE = "keep", "remove", "unreadable"


@dataclass
class DemoScan:
    path: Path
    status: str  # keep | remove | unreadable
    map_name: str = ""
    best_score: float = 0.0
    best_round: int = 0  # 1-based, as numbered in the tables
    best_play: str = ""
    plays: int = 0  # moments at or above the minimum score
    note: str = ""  # why the demo is not "keep" when it is not just a low score
    match: dict | None = None  # FACEIT match context (`match_summary`), None for other demos


def match_summary(block: dict | None, steamids: list[str] | None) -> dict | None:
    """The report's FACEIT `match` block without the roster, plus `team`/`opponent` of the first
    scanned player found in it (none when scanning for everyone)."""
    if block is None:
        return None
    summary = {k: v for k, v in block.items() if k != "players"}
    entry = next((block["players"][s] for s in steamids or [] if s in block.get("players", {})), None)
    if entry:
        summary.update(team=entry["team"], opponent=entry["opponent"])
    return summary


def match_cell(match: dict | None) -> str:
    """Table cell: `Team vs Opponent · Open`, or `A vs B · Open` without a scanned player's team."""
    if not match:
        return ""
    teams = [match["team"], match["opponent"]] if match.get("team") else match.get("teams", [])
    return " · ".join(p for p in (" vs ".join(teams), match.get("division", "")) if p)


def find_demos(folder: Path, recursive: bool) -> list[Path]:
    pattern = "**/*" if recursive else "*"
    return sorted(p for p in folder.glob(pattern) if p.is_file() and p.suffix.lower() == ".dem")


def classify(path: Path, map_name: str, moments: list[Moment], min_score: float) -> DemoScan:
    """`moments` are the player-eligible moments of the demo (already filtered to the players)."""
    if not moments:
        return DemoScan(path, REMOVE, map_name, note="no plays by these players")
    best = max(moments, key=lambda m: m.score)
    plays = sum(1 for m in moments if m.score >= min_score)
    return DemoScan(
        path,
        KEEP if plays else REMOVE,
        map_name,
        best.score,
        best.round_index + 1,
        " · ".join([best.describe(), *best.tags]),
        plays,
    )


def delete_demos(results: list[DemoScan]) -> tuple[list[Path], list[tuple[Path, str]]]:
    """Delete the demos with verdict `remove` (never `keep` or `unreadable`); -> (deleted, [(path, error)])."""
    deleted: list[Path] = []
    failed: list[tuple[Path, str]] = []
    for r in results:
        if r.status != REMOVE:
            continue
        try:
            r.path.unlink()
            deleted.append(r.path)
        except OSError as exc:
            failed.append((r.path, str(exc)))
    return deleted, failed


def write_scan(results: list[DemoScan], out: Path, min_score: float, players: list[str]) -> Path:
    """`out` = plain list (paths only, one per line, under KEEP / REMOVE headings); `out`.json = the details."""
    lines = [
        f"# Demo scan {date.today().isoformat()} - players: {', '.join(players) or 'everyone'}"
        f" - minimum score {min_score:g}",
        "# This list is written before anything is deleted. Lines starting with # are comments.",
    ]
    for status, heading in (
        (KEEP, "KEEP: has a play at or above the minimum score"),
        (REMOVE, "REMOVE: nothing good enough for these players"),
        (UNREADABLE, "UNREADABLE: could not be parsed - check these by hand"),
    ):
        group = [r for r in results if r.status == status]
        if not group:
            continue
        lines += ["", f"# {heading} ({len(group)})", *[str(r.path) for r in group]]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")

    details = out.with_suffix(".json")
    details.write_text(
        json.dumps(
            {
                "players": players,
                "min_score": min_score,
                "demos": [
                    {
                        "path": str(r.path),
                        "status": r.status,
                        "map": r.map_name,
                        "best_score": round(r.best_score, 2),
                        "best_round": r.best_round,
                        "best_play": r.best_play,
                        "plays_at_or_above_min": r.plays,
                        "note": r.note,
                        "match": r.match,
                    }
                    for r in results
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return details
