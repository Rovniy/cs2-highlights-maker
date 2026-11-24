"""Floor heights per map, so a camera does not "see" a level below its own.

The view checks (splice, static cams, b-roll) have no map geometry. On a stacked map like Nuke a
camera above A site looking down at an angle would otherwise see B site straight through the floor.
Floors come from where players actually stood: this demo's player positions (sampled once per
second of live play) plus the saved place map's cells when there is one (`highlights places build`).

The floor under a camera is the highest height at or below it where players stood at least
`MIN_SAMPLES` times, looked for right under the camera first and further out only when nobody stood
there (`SEARCH_RADII`, e.g. a camera behind a fence); a target more than `LEVEL_GAP` below that floor
is on another level. A camera with no player position near it is not checked. Heuristic, no walls: it only separates levels, it does not
stop a camera seeing through a wall on its own floor.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from math import floor

from highlights.demo.parser import DemoData
from highlights.places import CELL, Z_CELL, PlaceMap
from highlights.util.log import warn

Point = tuple[float, float, float]

# Below the floor under the camera by more than this = another level. Nuke: upper floor ~-416; B site
# (-768, its raised part, Observation and the bottom of Ramp at -640) is hidden, the rest of Ramp is not.
LEVEL_GAP = 200.0
MIN_SAMPLES = 2  # a height with fewer player samples (seconds) is a one-off, not a floor
HEIGHT_STEP = 32.0  # heights are counted in bins this tall
SEARCH_RADII = (1, 3, 6)  # horizontal search around the camera in CELL units (64/192/384 world units)
SAMPLE_SECONDS = 1.0

Sampler = Callable[[DemoData, list[int]], list[Point]]


class Floors:
    """How often players stood at each height, per horizontal cell column."""

    def __init__(self, heights: dict[tuple[int, int], Counter[float]]) -> None:
        self._columns = heights
        self._cache: dict[tuple[int, int, int], float | None] = {}

    @classmethod
    def from_points(cls, points: Iterable[Point], place_map: PlaceMap | None = None) -> Floors:
        columns: dict[tuple[int, int], Counter[float]] = {}
        for x, y, z in points:
            columns.setdefault((floor(x / CELL), floor(y / CELL)), Counter())[floor(z / HEIGHT_STEP) * HEIGHT_STEP] += 1
        if place_map is not None:
            # place cells are coarser (Z_CELL tall): their middle height
            for (ix, iy, iz), count in place_map.cell_counts().items():
                columns.setdefault((ix, iy), Counter())[(iz + 0.5) * Z_CELL] += count
        return cls(columns)

    def __len__(self) -> int:
        return len(self._columns)

    def floor_under(self, camera: Point) -> float | None:
        """Height of the floor under the camera, None when no player stood near."""
        key = (floor(camera[0] / 16), floor(camera[1] / 16), floor(camera[2] / 16))
        if key not in self._cache:
            self._cache[key] = self._floor_under(camera)
        return self._cache[key]

    def _floor_under(self, camera: Point) -> float | None:
        cx, cy = floor(camera[0] / CELL), floor(camera[1] / CELL)
        for radius in SEARCH_RADII:
            heights: Counter[float] = Counter()
            for ix in range(cx - radius, cx + radius + 1):
                for iy in range(cy - radius, cy + radius + 1):
                    for height, count in self._columns.get((ix, iy), {}).items():
                        if height <= camera[2]:
                            heights[height] += count
            floors = [h for h, n in heights.items() if n >= MIN_SAMPLES]
            if floors:
                return max(floors)
        return None

    def hides(self, camera: Point, target: Point) -> bool:
        """True when the target is on a level below the camera's."""
        under = self.floor_under(camera)
        return under is not None and target[2] < under - LEVEL_GAP


def sample_positions(demo: DemoData, ticks: list[int]) -> list[Point]:
    """Every player's position at the given ticks (an unreadable demo gives none)."""
    from highlights.demo.parser import is_parser_error
    from highlights.spatial import _query_positions

    try:
        positions, _, _ = _query_positions(demo.path, ticks)
    except BaseException as exc:
        if not is_parser_error(exc):
            raise
        return []
    return [(v["xyz"]["x"], v["xyz"]["y"], v["xyz"]["z"]) for players in positions.values() for v in players.values()]


def floors_for(demo: DemoData, place_map: PlaceMap | None = None, sampler: Sampler = sample_positions) -> Floors | None:
    """Floors from this demo's live play plus the place map; None (no level check) when both are empty."""
    step = max(1, round(SAMPLE_SECONDS * demo.tickrate))
    ticks = sorted({t for r in demo.rounds for t in range(r.freeze_end_tick, r.end_tick + 1, step)})
    floors = Floors.from_points(sampler(demo, ticks) if ticks else [], place_map)
    if not len(floors):
        warn(f"No player positions for {demo.map_name}: cameras are not checked for being on another level")
        return None
    return floors
