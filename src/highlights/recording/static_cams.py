"""--static-cam: a fixed camera over the play, recorded as its own take for editing by hand.

Static cams live next to the map's cam paths (the same gitignored folder, never in the repo):
    campaths/<Map>/static.toml          one table per cam, e.g. pasted from `getpos` in free cam:
        [a-site]
        getpos = "setpos -1234.5 567.8 128.0;setang 12.5 90.0 0.0"
        fov = 90                         # optional, default 90
        label = "A site from balcony"    # optional
      (or pos = [x, y, z] and angles = [pitch, yaw] instead of getpos)
    campaths/<Map>/static/*.campath     HLAE cam paths; the first point is the pose (one point is enough)

Each take holds the one pose for the whole clip (a generated path of MIN_POINTS identical poses: HLAE's
cubic interpolation plays nothing with fewer than 4), records over the same ticks as its POV clip and is
written as a separate file, never spliced or concatenated.
Matching (`auto`/`all`): a cam qualifies when a selected kill's victim dies in its view, or the POV
player is in view for at least MIN_SEGMENT seconds; best = most deaths seen, then most player time,
then closest. Same view-cone test as splice mode, so the same caveats: no wall check, campath space
assumed to equal demo world space, and `getpos` assumed to give the free-cam eye position.
"""

from __future__ import annotations

import re
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from math import asin, atan2, degrees, dist
from pathlib import Path

from rich.markup import escape

from highlights.floors import Floors
from highlights.models import Sequence
from highlights.recording.campaths import CAMPATH_SUFFIXES, _forward, first_by_name, map_folders
from highlights.recording.cinematic import _action_targets, _target_at
from highlights.recording.dyn_sequences import insert_takes
from highlights.recording.dyncams import MIN_POINTS, Pose, write_campath
from highlights.recording.splice_plan import MIN_SEGMENT, SAMPLE_STEP, Point, _in_view
from highlights.util.log import info, warn

STATIC_FILE = "static.toml"
STATIC_DIR = "static"
DEFAULT_FOV = 90.0
NUMBER = r"(-?\d+(?:\.\d+)?)"
# `getpos` prints "setpos x y z;setang p y r", `getpos_exact` "setpos_exact ...;setang_exact ..."
GETPOS = re.compile(
    rf"setpos(?:_exact)?\s+{NUMBER}\s+{NUMBER}\s+{NUMBER}\s*;?\s*setang(?:_exact)?\s+{NUMBER}\s+{NUMBER}", re.IGNORECASE
)


@dataclass(frozen=True)
class StaticCam:
    name: str
    pos: Point
    pitch: float  # degrees, positive looks down
    yaw: float  # degrees, positive turns toward +y
    fov: float = DEFAULT_FOV
    label: str = ""

    @property
    def forward(self) -> Point:
        return _forward({"ry": str(self.pitch), "rz": str(self.yaw)})

    @property
    def slug(self) -> str:
        return re.sub(r"[^a-z0-9]+", "-", self.name.lower()).strip("-") or "cam"


def parse_getpos(text: str) -> tuple[Point, float, float] | None:
    """(pos, pitch, yaw) from `setpos x y z;setang pitch yaw roll` (roll is ignored)."""
    found = GETPOS.search(text)
    if not found:
        return None
    x, y, z, pitch, yaw = (float(v) for v in found.groups())
    return (x, y, z), pitch, yaw


def _from_table(name: str, entry: dict) -> StaticCam | None:
    if "getpos" in entry:
        parsed = parse_getpos(str(entry["getpos"]))
        if parsed is None:
            warn(f"Static cam {name}: cannot read getpos {entry['getpos']!r} (expected 'setpos x y z;setang p y r')")
            return None
        pos, pitch, yaw = parsed
    else:
        try:
            pos = tuple(float(v) for v in entry["pos"])
            pitch, yaw = (float(v) for v in entry["angles"][:2])
        except (KeyError, TypeError, ValueError):
            warn(f"Static cam {name}: needs getpos, or pos = [x, y, z] and angles = [pitch, yaw]")
            return None
        if len(pos) != 3:
            warn(f"Static cam {name}: pos needs three numbers")
            return None
    return StaticCam(name, pos, pitch, yaw, float(entry.get("fov", DEFAULT_FOV)), str(entry.get("label", "")))


def _from_campath(file: Path) -> StaticCam | None:
    try:
        points = [p.attrib for p in ET.parse(file).getroot().iter("p") if "t" in p.attrib]
    except (ET.ParseError, OSError) as exc:
        warn(f"Skipping static cam {file.name}: {exc}")
        return None
    try:
        first = min(points, key=lambda a: float(a["t"]))
        forward = _forward(first)
        pos = (float(first["x"]), float(first["y"]), float(first["z"]))
    except (KeyError, ValueError):
        warn(f"Skipping static cam {file.name}: no usable point")
        return None
    pitch = degrees(-asin(max(-1.0, min(1.0, forward[2]))))
    yaw = degrees(atan2(forward[1], forward[0]))
    return StaticCam(file.stem, pos, pitch, yaw, float(first.get("fov", DEFAULT_FOV)))


def load_static_cams(bases: Path | list[Path], map_name: str) -> list[StaticCam]:
    """Static cams for a map: per map folder `static.toml` entries, then `static/` cam path files."""
    cams: list[StaticCam] = []
    for folder in map_folders(bases, map_name):
        table = folder / STATIC_FILE
        if table.is_file():
            try:
                data = tomllib.loads(table.read_text(encoding="utf-8"))
            except (tomllib.TOMLDecodeError, OSError) as exc:
                warn(f"Skipping {table}: {exc}")
                data = {}
            for name, entry in data.items():
                if isinstance(entry, dict) and (cam := _from_table(name, entry)):
                    cams.append(cam)
        files = folder / STATIC_DIR
        if files.is_dir():
            for file in sorted(files.iterdir()):
                if file.suffix.lower() in CAMPATH_SUFFIXES and (cam := _from_campath(file)):
                    cams.append(cam)
    return first_by_name(cams, "static cam")


def _victims(spatial: dict) -> list[tuple[str, Point]]:
    """(victim steamid, position at death) of the play's selected kills."""
    out = []
    for e in spatial.get("engagements", []):
        xyz = (e.get("victim") or {}).get("xyz")
        if e.get("is_selected_moment_kill") and xyz:
            out.append((e["victim_steamid"], (xyz["x"], xyz["y"], xyz["z"])))
    return out


def assess(
    cam: StaticCam, sequence: Sequence, spatial: dict, tickrate: float, names: dict[str, str], floors: Floors | None = None
) -> dict:
    """What the cam sees of a play: deaths in view, seconds of the POV player in view, distance."""
    forward = cam.forward
    seen = [names.get(s, s) for s, pos in _victims(spatial) if _in_view(cam.pos, forward, cam.fov, pos, floors=floors)]
    target_at = _target_at(sequence, spatial, tickrate)
    steps = int(sequence.duration_seconds(tickrate) / SAMPLE_STEP) + 1
    in_view = sum(
        1 for i in range(steps)
        if (p := target_at(i * SAMPLE_STEP)) is not None and _in_view(cam.pos, forward, cam.fov, p, floors=floors)
    )
    targets = _action_targets(spatial)
    distance = sum(dist(cam.pos, t) for t in targets) / len(targets) if targets else float("inf")
    return {
        "name": cam.name,
        "label": cam.label,
        "deaths_seen": seen,
        "player_seconds": round(in_view * SAMPLE_STEP, 1),
        "distance": round(distance, 1) if targets else None,
    }


def _rank(assessments: list[dict]) -> list[dict]:
    eligible = [a for a in assessments if a["deaths_seen"] or a["player_seconds"] >= MIN_SEGMENT]
    return sorted(
        eligible,
        key=lambda a: (-len(a["deaths_seen"]), -a["player_seconds"], a["distance"] if a["distance"] is not None else 1e12),
    )


def parse_mode(value: str | None) -> str | list[str] | None:
    """--static-cam: None, "auto", "all" or a list of cam names."""
    if not value:
        return None
    if value.lower() in ("auto", "all"):
        return value.lower()
    return [n.strip() for n in value.split(",") if n.strip()]


def add_static_sequences(
    sequences: list[Sequence],
    cams: list[StaticCam],
    spatial_by_sequence: dict[int, dict],
    tickrate: float,
    mode: str | list[str],
    out_dir: Path,
    names: dict[str, str] | None = None,
    floors: Floors | None = None,
) -> tuple[list[Sequence], dict[int, dict]]:
    """Add static-cam takes after each POV sequence's other takes.

    `auto`: the best qualifying cam per play; `all`: every qualifying cam; a list of names: those
    cams for every play (warns about unknown names and about cams that see nothing of a play)."""
    by_name = {c.name.lower(): c for c in cams}
    if isinstance(mode, list):
        unknown = [n for n in mode if n.lower() not in by_name]
        if unknown:
            available = ", ".join(c.name for c in cams) or "none"
            warn(escape(f"No static cam named {', '.join(unknown)}; available: {available}"))
    new_for: dict[int, list[Sequence]] = {}
    for pov in (s for s in sequences if s.cinematic_of is None):
        spatial = spatial_by_sequence.get(pov.number, {})
        looks = {c.name: assess(c, pov, spatial, tickrate, names or {}, floors) for c in cams}
        if isinstance(mode, list):
            chosen = [looks[by_name[n.lower()].name] for n in mode if n.lower() in by_name]
            for look in chosen:
                if not look["deaths_seen"] and look["player_seconds"] < MIN_SEGMENT:
                    warn(escape(f"{pov.label}: static cam {look['name']} sees no death and little of the player"))
        else:
            ranked = _rank(list(looks.values()))
            chosen = ranked[:1] if mode == "auto" else ranked
            if not chosen:
                warn(f"{pov.label}: no static cam sees a death or the player, no static take")
                continue
        duration = pov.duration_seconds(tickrate)
        takes = []
        for look in chosen:
            cam = next(c for c in cams if c.name == look["name"])
            pose = Pose(cam.pos, cam.yaw, cam.pitch, cam.fov)
            hold = [(duration * i / (MIN_POINTS - 1), pose) for i in range(MIN_POINTS)]
            file = write_campath(out_dir / f"static_{cam.slug}_{pov.start_tick}.campath", hold)
            seen = f"sees {', '.join(look['deaths_seen'])} die" if look["deaths_seen"] else "sees no death"
            info(escape(f"{pov.label}: static cam {cam.name} — {seen}, player in view {look['player_seconds']:.1f}s"))
            takes.append(
                replace(
                    pov,
                    label=f"{pov.label} — static {cam.name}",
                    campath=file,
                    cinematic_of=pov.number,
                    splice_segments=None,
                    campath_retime=None,
                    slowmo=None,
                    tilt=None,
                    cinematic_candidates=None,
                    dynamic_cam=None,
                    static_cam={**look, "slug": cam.slug},
                )
            )
        if takes:
            new_for[pov.number] = takes
    return insert_takes(sequences, spatial_by_sequence, new_for)
