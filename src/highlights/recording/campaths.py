"""User-supplied HLAE cam paths for --cinematic recordings.

The paths themselves are never part of this repository: they are read from a
gitignored folder (default <repo>/campaths, one subfolder per map) at run time.
Files are HLAE campath XML (`<campath><points><p t x y z .../>`); `.dz`,
`.campath` and `.xml` are accepted.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from math import cos, radians, sin, sqrt
from pathlib import Path

from highlights.config import Config
from highlights.util.log import warn

CAMPATH_SUFFIXES = {".dz", ".campath", ".xml"}
# Height difference counts this much more than horizontal distance, so same-floor
# paths outrank paths on another level.
VERTICAL_WEIGHT = 2.0


@dataclass(frozen=True)
class Keyframe:
    t: float  # seconds since the first point
    pos: tuple[float, float, float]  # x, y, z in HLAE campath space
    forward: tuple[float, float, float]  # unit view direction (roll is ignored)
    fov: float  # degrees, taken as horizontal


@dataclass(frozen=True)
class CamPath:
    name: str  # file stem, e.g. "A4"
    file: Path
    keyframes: tuple[Keyframe, ...]
    duration: float  # seconds between the first and last point

    @property
    def points(self) -> tuple[tuple[float, float, float], ...]:
        return tuple(k.pos for k in self.keyframes)


def default_campaths_dir() -> Path:
    # src/highlights/recording/campaths.py -> repo root
    return Path(__file__).resolve().parents[3] / "campaths"


def resolve_campaths_dirs(config: Config) -> list[Path]:
    """The cam path folders to read, in priority order. `campaths_dir` is one folder or a list
    (e.g. a free library and a paid one); "auto" stands for <repo>/campaths."""
    value = config.paths.campaths_dir
    entries = [value] if isinstance(value, str) else list(value)
    dirs: list[Path] = []
    for entry in entries:
        folder = default_campaths_dir() if entry == "auto" else Path(entry).expanduser()
        if folder not in dirs:
            dirs.append(folder)
    return dirs or [default_campaths_dir()]


def describe_dirs(dirs: list[Path]) -> str:
    return ", ".join(str(d) for d in dirs)


def _normalize(v: tuple[float, float, float]) -> tuple[float, float, float]:
    length = sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2) or 1.0
    return (v[0] / length, v[1] / length, v[2] / length)


def _forward(attrib: dict[str, str]) -> tuple[float, float, float]:
    """View direction of a point. Quaternion wins over Euler angles when both are
    present (HLAE's rule). Quake space: x forward, y left, z up; positive pitch
    looks down, positive yaw turns left."""
    if all(key in attrib for key in ("qw", "qx", "qy", "qz")):
        w, x, y, z = (float(attrib[key]) for key in ("qw", "qx", "qy", "qz"))
        return _normalize((1 - 2 * (y * y + z * z), 2 * (x * y + w * z), 2 * (x * z - w * y)))
    pitch, yaw = radians(float(attrib["ry"])), radians(float(attrib["rz"]))
    return (cos(pitch) * cos(yaw), cos(pitch) * sin(yaw), -sin(pitch))


def parse_campath(file: Path) -> CamPath | None:
    """Read a campath file; None when it has fewer than two usable points."""
    try:
        root = ET.parse(file).getroot()
    except (ET.ParseError, OSError) as exc:
        warn(f"Skipping cam path {file.name}: {exc}")
        return None
    raw: list[Keyframe] = []
    for p in root.iter("p"):
        try:
            raw.append(
                Keyframe(
                    t=float(p.attrib["t"]),
                    pos=(float(p.attrib["x"]), float(p.attrib["y"]), float(p.attrib["z"])),
                    forward=_forward(p.attrib),
                    fov=float(p.attrib.get("fov", 90)),
                )
            )
        except (KeyError, ValueError):
            continue
    if len(raw) < 2:
        warn(f"Skipping cam path {file.name}: fewer than two points")
        return None
    raw.sort(key=lambda k: k.t)
    start = raw[0].t
    keyframes = tuple(Keyframe(k.t - start, k.pos, k.forward, k.fov) for k in raw)
    return CamPath(name=file.stem, file=file.resolve(), keyframes=keyframes, duration=keyframes[-1].t)


def retime(path: CamPath, offset: float = 0.0, hold_until: float | None = None) -> CamPath:
    """The path started `offset` seconds late, holding its first pose until then and its
    last pose until `hold_until` (seconds from the clip start; never shortens the path)."""
    keys = list(path.keyframes)
    if offset > 0:
        first = keys[0]
        keys = [first, *(Keyframe(k.t + offset, k.pos, k.forward, k.fov) for k in keys)]
    if hold_until is not None and hold_until > keys[-1].t + 1e-6:
        last = keys[-1]
        keys.append(Keyframe(hold_until, last.pos, last.forward, last.fov))
    return CamPath(name=path.name, file=path.file, keyframes=tuple(keys), duration=keys[-1].t)


def write_retimed(path: CamPath, offset: float, hold_until: float | None, out_file: Path) -> Path:
    """Write a copy of the path's file with the same shift/hold as `retime` (the original is
    only read). Point attributes are kept verbatim, so poses are exactly the original's."""
    tree = ET.parse(path.file)
    points = [p for p in tree.getroot().iter("p") if "t" in p.attrib]
    parent = {child: parent for parent in tree.getroot().iter() for child in parent}
    points.sort(key=lambda p: float(p.attrib["t"]))
    start = float(points[0].attrib["t"])
    for p in points:
        p.attrib["t"] = repr(float(p.attrib["t"]) + (offset if offset > 0 else 0.0))
    container = parent[points[0]]
    index = list(container).index(points[0])
    if offset > 0:
        head = ET.Element("p", dict(points[0].attrib))
        head.attrib["t"] = repr(start)
        container.insert(index, head)
    last = points[-1]
    end = float(last.attrib["t"]) - start
    if hold_until is not None and hold_until > end + 1e-6:
        tail = ET.Element("p", dict(last.attrib))
        tail.attrib["t"] = repr(start + hold_until)
        container.insert(list(container).index(last) + 1, tail)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    tree.write(out_file, encoding="utf-8", xml_declaration=True)
    return out_file


def pose_at(path: CamPath, t: float) -> tuple[tuple[float, float, float], tuple[float, float, float], float] | None:
    """(position, forward, fov) of the camera t seconds after the path starts, or
    None outside the path. Linear interpolation (HLAE's own curve is smoother)."""
    keys = path.keyframes
    if t < 0 or t > path.duration:
        return None
    for a, b in zip(keys, keys[1:]):
        if a.t <= t <= b.t:
            w = 0.0 if b.t == a.t else (t - a.t) / (b.t - a.t)
            lerp = lambda u, v: tuple(x + (y - x) * w for x, y in zip(u, v))  # noqa: E731
            return lerp(a.pos, b.pos), _normalize(lerp(a.forward, b.forward)), a.fov + (b.fov - a.fov) * w
    return keys[-1].pos, keys[-1].forward, keys[-1].fov


def map_folders(bases: Path | list[Path], map_name: str) -> list[Path]:
    """Subfolders named like the map ("Nuke" or "de_nuke"): per base in the given order, sorted within one."""
    wanted = {map_name.lower(), map_name.lower().removeprefix("de_")}
    folders: list[Path] = []
    for base in [bases] if isinstance(bases, Path) else bases:
        if base.is_dir():
            folders += [f for f in sorted(base.iterdir()) if f.is_dir() and f.name.lower() in wanted]
    return folders


def first_by_name(items: list, kind: str) -> list:
    """Drop items whose name (case-insensitive) an earlier one already has: names identify cams
    everywhere (--cinematic-path, labels, work files), so the earlier folder wins."""
    kept: dict[str, object] = {}
    for item in items:
        key = item.name.lower()
        if key in kept:
            warn(f"Two {kind}s named {item.name!r}: using the first one found, rename one to use both")
            continue
        kept[key] = item
    return list(kept.values())


def load_campaths(bases: Path | list[Path], map_name: str) -> list[CamPath]:
    """Cam paths for a map, from the subfolders named like it ("Nuke" or "de_nuke") in every base."""
    paths: list[CamPath] = []
    for folder in map_folders(bases, map_name):
        for file in sorted(folder.iterdir()):
            if file.suffix.lower() in CAMPATH_SUFFIXES and (parsed := parse_campath(file)):
                paths.append(parsed)
    return first_by_name(paths, "cam path")


def _weighted_distance(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (VERTICAL_WEIGHT * (a[2] - b[2])) ** 2)


def path_distance(path: CamPath, targets: list[tuple[float, float, float]]) -> float:
    """How close a path passes to the action: mean over targets of the nearest path
    point, in world units with height difference weighted up. Assumes campath space
    equals demo world space — not yet validated in-game."""
    return sum(min(_weighted_distance(point, target) for point in path.points) for target in targets) / len(targets)


def rank_campaths(campaths: list[CamPath], targets: list[tuple[float, float, float]]) -> list[CamPath]:
    """Order paths by how close they pass to the action."""
    if not targets:
        return []
    return sorted(campaths, key=lambda path: (path_distance(path, targets), path.name))
