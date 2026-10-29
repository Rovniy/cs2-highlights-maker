"""Resolve a --player query (steamid64 or name) against the demo's players."""

from __future__ import annotations

from collections.abc import Iterable

from highlights.demo.parser import DemoData


class PlayerNotFound(ValueError):
    pass


def resolve_player(demo: DemoData, query: str) -> str:
    """Return the steamid64 for a steamid or (partial, case-insensitive) name."""
    query = query.strip()
    if query.isdigit() and len(query) == 17:
        if query in demo.player_names:
            return query
        raise PlayerNotFound(f"SteamID {query} not found in this demo")

    lowered = query.lower()
    exact = [sid for sid, name in demo.player_names.items() if name.lower() == lowered]
    if len(exact) == 1:
        return exact[0]
    partial = [sid for sid, name in demo.player_names.items() if lowered in name.lower()]
    if len(partial) == 1:
        return partial[0]
    if len(partial) > 1:
        names = ", ".join(sorted(demo.player_names[sid] for sid in partial))
        raise PlayerNotFound(f"'{query}' matches several players: {names}")
    available = ", ".join(sorted(demo.player_names.values()))
    raise PlayerNotFound(f"Player '{query}' not found. Players in demo: {available}")


def resolve_players(demo: DemoData, queries: Iterable[str]) -> list[str]:
    """Resolve comma-separated steamid/name queries to de-duplicated steamid64s."""
    pieces = [q for item in queries for q in (p.strip() for p in item.split(",")) if q]
    if not pieces:
        raise PlayerNotFound("No players given")
    resolved: list[str] = []
    for piece in pieces:
        sid = resolve_player(demo, piece)
        if sid not in resolved:
            resolved.append(sid)
    return resolved
