"""Per-map "position -> place name" lookup, e.g. "Catwalk", "BombsiteA".

Place names are the map's own area names, sampled from demos (a player's
`last_place_name`) and binned into a coarse 3D grid, so positions can be labelled
without parsing a demo again.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from math import floor, sqrt
from pathlib import Path

from highlights.config import app_data_dir

# Horizontal bin size, world units.
CELL = 64.0
# Vertical bin size; keeps floors apart.
Z_CELL = 96.0
# Horizontal search radius in cells when looking up the nearest bin.
SEARCH_CELLS = 2
# Height difference counts this much more than horizontal distance, so same-
# floor cells outrank cells on another level.
VERTICAL_WEIGHT = 2.0

Point = tuple[float, float, float]


class PlaceMap:
    """Map world positions to place names via a grid of counted names."""

    def __init__(self, map_name: str) -> None:
        self.map_name = map_name
        # (ix, iy, iz) -> Counter of place names observed in that cell.
        self._cells: dict[tuple[int, int, int], Counter[str]] = {}

    def _cell_of(self, x: float, y: float, z: float) -> tuple[int, int, int]:
        return (floor(x / CELL), floor(y / CELL), floor(z / Z_CELL))

    def add(self, x: float, y: float, z: float, place: str | None) -> None:
        """Record one place-name sample at a world position."""
        if not place or not place.strip():
            return
        name = place.strip()
        cell = self._cell_of(x, y, z)
        counter = self._cells.get(cell)
        if counter is None:
            counter = self._cells[cell] = Counter()
        counter[name] += 1

    def add_many(self, samples: Iterable[tuple[float, float, float, str | None]]) -> None:
        """Record many (x, y, z, place) samples."""
        for x, y, z, place in samples:
            self.add(x, y, z, place)

    def lookup(self, x: float, y: float, z: float) -> str | None:
        """Most common place name of the nearest populated cell, or None."""
        cx, cy, cz = self._cell_of(x, y, z)
        best: tuple[float, tuple[int, int, int]] | None = None
        for ix in range(cx - SEARCH_CELLS, cx + SEARCH_CELLS + 1):
            for iy in range(cy - SEARCH_CELLS, cy + SEARCH_CELLS + 1):
                for iz in range(cz - 1, cz + 2):
                    if (ix, iy, iz) not in self._cells:
                        continue
                    centre = ((ix + 0.5) * CELL, (iy + 0.5) * CELL, (iz + 0.5) * Z_CELL)
                    dist = sqrt(
                        (x - centre[0]) ** 2
                        + (y - centre[1]) ** 2
                        + (VERTICAL_WEIGHT * (z - centre[2])) ** 2
                    )
                    key = (dist, (ix, iy, iz))
                    if best is None or key < (best[0], best[1]):
                        best = key
        if best is None:
            return None
        counter = self._cells[best[1]]
        top = max(counter.values())
        return min(name for name, count in counter.items() if count == top)

    def __len__(self) -> int:
        return len(self._cells)

    def cell_counts(self) -> dict[tuple[int, int, int], int]:
        """Samples per grid cell (used for floor heights)."""
        return {cell: sum(counter.values()) for cell, counter in self._cells.items()}

    def to_dict(self) -> dict:
        """JSON-friendly snapshot (cell keys are comma-joined ints)."""
        return {
            "map": self.map_name,
            "cell": CELL,
            "z_cell": Z_CELL,
            "cells": {
                f"{ix},{iy},{iz}": dict(counter)
                for (ix, iy, iz), counter in self._cells.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict) -> PlaceMap:
        """Rebuild a map; raises ValueError when the grid size changed."""
        if data["cell"] != CELL or data["z_cell"] != Z_CELL:
            raise ValueError("place map was built with a different grid size")
        place_map = cls(data["map"])
        for key, counter in data["cells"].items():
            ix, iy, iz = (int(part) for part in key.split(","))
            place_map._cells[(ix, iy, iz)] = Counter(counter)
        return place_map


def places_dir() -> Path:
    """Folder holding one JSON per map, under the app data directory."""
    return app_data_dir() / "places"


def places_path(map_name: str, directory: Path | None = None) -> Path:
    return (directory or places_dir()) / f"{map_name.lower()}.json"


def save_places(place_map: PlaceMap, directory: Path | None = None) -> Path:
    """Write the map as compact JSON and return the file path."""
    path = places_path(place_map.map_name, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(place_map.to_dict(), indent=None, separators=(",", ":")),
        encoding="utf-8",
    )
    return path


def load_places(map_name: str, directory: Path | None = None) -> PlaceMap | None:
    """Read a saved map; None when missing or unreadable."""
    path = places_path(map_name, directory)
    try:
        return PlaceMap.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def compress_route(names: Iterable[str | None]) -> list[str]:
    """Drop empty names and collapse consecutive duplicates, keeping order."""
    route: list[str] = []
    for name in names:
        if not name or not name.strip():
            continue
        stripped = name.strip()
        if not route or route[-1] != stripped:
            route.append(stripped)
    return route


def route_of(points: Iterable[Point], place_map: PlaceMap) -> list[str]:
    """Place names a player walks through, compressed (unknown points skipped)."""
    return compress_route(place_map.lookup(*point) for point in points)
