"""Where a cam path goes: place names along the route, plus optional labels.

Routes come from the sampled camera positions of a path fed through the map's
place lookup (`highlights.places`). Labels are optional per-map `labels.toml`
files next to the cam path folders, mapping file names to human-readable
descriptions so names like "B5" stop being opaque.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from highlights.places import PlaceMap, route_of
from highlights.recording.campaths import CamPath, map_folders, pose_at
from highlights.util.log import warn


def path_route(path: CamPath, place_map: PlaceMap, step: float = 0.5) -> list[str]:
    """Place names the camera passes through, compressed (e.g. ["Catwalk", "ASite"]).

    Positions are sampled every `step` seconds from the start up to and including
    the final time; unknown positions are skipped.
    """
    positions: list[tuple[float, float, float]] = []
    t = 0.0
    while t < path.duration:
        pose = pose_at(path, t)
        if pose:
            positions.append(pose[0])
        t += step
    pose = pose_at(path, path.duration)
    if pose:
        positions.append(pose[0])
    return route_of(positions, place_map)


def load_labels(bases: Path | list[Path], map_name: str) -> dict[str, str]:
    """Cam path labels for a map, from `labels.toml` in its folder(s).

    Every top-level string value becomes a label keyed by the lowercased file
    stem (the first folder wins, as for the paths); a missing or invalid file contributes nothing."""
    labels: dict[str, str] = {}
    for folder in map_folders(bases, map_name):
        file = folder / "labels.toml"
        if not file.is_file():
            continue
        try:
            data = tomllib.loads(file.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            warn(f"Skipping {file}: {exc}")
            continue
        if not isinstance(data, dict):
            warn(f"Skipping {file}: expected a table of labels")
            continue
        for key, value in data.items():
            if not isinstance(value, str) or not value.strip():
                continue
            labels.setdefault(key.lower(), value.strip())
    return labels
