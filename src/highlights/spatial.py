"""Spatial timeline for CS2 highlight clips (Duality/HLAE camera matching).

Install alongside report.py in src/highlights/. This is analysis-only; it does
not modify the recording commands, camera positions, or game installation.

Coordinates are raw Source 2 world coordinates. No camera-path format is assumed.
All timestamps are nominal DEMO times, not measured video presentation timestamps.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from math import dist, isfinite
from pathlib import Path
from typing import Any

import pandas as pd
from demoparser2 import DemoParser

from highlights.demo.parser import DemoData, DemoParseError, is_parser_error
from highlights.models import Sequence
from highlights.routes import clip_route


SAMPLES_PER_SECOND = 4
# A coarse engagement-clustering tolerance in raw CS2 world units, not map callouts.
ZONE_SPLIT_DISTANCE = 700.0
ZONE_SPLIT_HEIGHT = 180.0


def _float(value: object) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        result = float(value)
        return round(result, 3) if isfinite(result) else None
    except (ValueError, TypeError):
        return None


def _steamid(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    if isinstance(value, (int, float)):
        return str(int(value))
    return str(value).strip()


def _point(sample: dict[str, Any] | None) -> dict[str, float] | None:
    return sample.get("xyz") if sample else None


def _midpoint(a: dict[str, float] | None, b: dict[str, float] | None) -> dict[str, float] | None:
    if a and b:
        return {key: round((a[key] + b[key]) / 2, 3) for key in ("x", "y", "z")}
    return a or b


def _distance(a: dict[str, float], b: dict[str, float]) -> float:
    return dist((a["x"], a["y"], a["z"]), (b["x"], b["y"], b["z"]))


def _mean(points: list[dict[str, float]]) -> dict[str, float] | None:
    if not points:
        return None
    return {axis: round(sum(p[axis] for p in points) / len(points), 3) for axis in ("x", "y", "z")}


def _get_sample(
    positions: dict[int, dict[str, dict[str, Any]]],
    tick: int,
    steamid: str,
    fallback_before: bool = True,
) -> dict[str, Any] | None:
    # At a kill tick the victim may already be dead/absent. The preceding tick
    # is queried explicitly so the victim's last alive position remains usable.
    exact = positions.get(tick, {}).get(steamid)
    if exact is not None:
        return exact
    if fallback_before:
        return positions.get(tick - 1, {}).get(steamid)
    return None


def _query_positions(
    demo_path: Path,
    ticks: list[int],
) -> tuple[dict[int, dict[str, dict[str, Any]]], list[str], str | None]:
    """Fetch only requested ticks. Optional properties degrade to XYZ safely."""
    parser = DemoParser(str(demo_path))
    # Different builds of demoparser2 may expose different optional props.
    # Keep the basic XYZ extraction working even if place hints are unavailable.
    prop_options = [
        ["X", "Y", "Z", "yaw", "pitch", "last_place_name", "is_alive", "duck_amount"],
        ["X", "Y", "Z", "yaw", "pitch", "last_place_name", "is_alive"],
        ["X", "Y", "Z", "yaw", "pitch", "last_place_name"],
        ["X", "Y", "Z", "yaw", "pitch"],
        ["X", "Y", "Z"],
    ]
    note = None
    frame = None
    properties = prop_options[0]
    for option in prop_options:
        try:
            frame = parser.parse_ticks(option, ticks=ticks)
            properties = option
            if option != prop_options[0]:
                note = "Some optional player props were unavailable; partial spatial metadata returned."
            break
        except BaseException as exc:
            if not is_parser_error(exc):
                raise
            if option == prop_options[-1]:
                raise

    if frame is None or frame.empty:
        return {}, properties, note or "No position snapshots available at requested ticks."

    if not {"tick", "steamid", "X", "Y", "Z"}.issubset(frame.columns):
        raise ValueError(
            "demoparser2 position query did not return required tick, steamid, X, Y, Z columns; "
            f"received {sorted(frame.columns)}"
        )

    positions: dict[int, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in frame.to_dict(orient="records"):
        x, y, z = (_float(row.get(key)) for key in ("X", "Y", "Z"))
        if x is None or y is None or z is None:
            continue
        sid = _steamid(row.get("steamid"))
        if not sid:
            continue
        try:
            tick = int(row["tick"])
        except (TypeError, ValueError):
            continue
        place = row.get("last_place_name")
        if place is None or pd.isna(place) or not str(place).strip():
            place = None
        else:
            place = str(place).strip()
        is_alive = row.get("is_alive")
        if is_alive is not None and not pd.isna(is_alive):
            is_alive = bool(is_alive)
        else:
            is_alive = None
        positions[tick][sid] = {
            "xyz": {"x": x, "y": y, "z": z},
            "yaw": _float(row.get("yaw")),
            "pitch": _float(row.get("pitch")),
            "place_name_hint": place,
            "is_alive": is_alive,
            "duck_amount": _float(row.get("duck_amount")),
        }
    return positions, properties, note


def sample_places(demo_path: Path, ticks: list[int]) -> list[tuple[float, float, float, str]]:
    """Sample (x, y, z, place) of alive players at the given ticks for the place lookup."""
    if not ticks:
        return []
    try:
        parser = DemoParser(str(demo_path))
        frame = parser.parse_ticks(["X", "Y", "Z", "last_place_name", "is_alive"], ticks=ticks)
    except BaseException as exc:
        if not is_parser_error(exc):
            raise
        detail = str(exc)[:120]
        raise DemoParseError(
            f"{demo_path.name}: could not sample places ({type(exc).__name__}: {detail})"
        ) from exc
    if frame is None or frame.empty:
        return []
    samples: list[tuple[float, float, float, str]] = []
    for row in frame.to_dict(orient="records"):
        x, y, z = (_float(row.get(key)) for key in ("X", "Y", "Z"))
        if x is None or y is None or z is None:
            continue
        place = row.get("last_place_name")
        if place is None or pd.isna(place) or not str(place).strip():
            continue
        is_alive = row.get("is_alive")
        if is_alive is not None and not pd.isna(is_alive) and not bool(is_alive):
            continue
        samples.append((x, y, z, str(place).strip()))
    return samples


TRACK_HZ = 16  # position samples per second for generated cams
CROUCH_EYE_DROP = 18.0  # standing eye height (64) minus crouched eye height (46), world units


def sample_pov_tracks(
    demo: DemoData,
    requests: dict[int, tuple[Sequence, list[tuple[float, float]]]],
) -> dict[int, dict[str, list[tuple[float, tuple[float, float, float], float, float]]]]:
    """Dense (clip seconds, xyz, yaw, pitch) tracks of each sequence's camera players.

    z is the feet height minus the crouch drop (`duck_amount` x `CROUCH_EYE_DROP`, 0 when the demo
    has no `duck_amount`), so adding the standing eye height (dyncams.EYE_HEIGHT) gives the player's
    real eye height whether they stand or crouch.

    `requests` maps a sequence number to the sequence and the clip-second ranges
    that need data. One demo query serves every sequence; an unreadable demo gives
    an empty result, so generated cams are skipped instead of failing the run.
    """
    rate = float(demo.tickrate)
    step = max(1, round(rate / TRACK_HZ))
    wanted: dict[int, set[int]] = {}
    for number, (sequence, ranges) in requests.items():
        ticks: set[int] = set()
        for a, b in ranges:
            first = sequence.start_tick + int(a * rate)
            last = min(sequence.end_tick, sequence.start_tick + int(b * rate) + step)
            ticks.update(range(first, last + 1, step))
            ticks.add(last)
        wanted[number] = ticks
    all_ticks = sorted(set().union(*wanted.values())) if wanted else []
    if not all_ticks:
        return {}
    try:
        positions, _, _ = _query_positions(demo.path, all_ticks)
    except BaseException as exc:
        if not is_parser_error(exc):
            raise
        return {}
    result: dict[int, dict[str, list]] = {}
    for number, (sequence, _) in requests.items():
        tracks: dict[str, list] = {}
        for sid in sequence.featured_steamids:
            samples = []
            for tick in sorted(wanted[number]):
                value = positions.get(tick, {}).get(sid)
                if not value or value.get("yaw") is None or value.get("pitch") is None:
                    continue
                xyz = value["xyz"]
                drop = CROUCH_EYE_DROP * min(1.0, max(0.0, value.get("duck_amount") or 0.0))
                samples.append(((tick - sequence.start_tick) / rate, (xyz["x"], xyz["y"], xyz["z"] - drop), value["yaw"], value["pitch"]))
            if samples:
                tracks[sid] = samples
        result[number] = tracks
    return result


GRENADE_KINDS = {
    "CHEGrenadeProjectile": "he",
    "CFlashbangProjectile": "flash",
    "CMolotovProjectile": "molotov",  # molotov and incendiary share it
    "CSmokeGrenadeProjectile": "smoke",
    "CDecoyProjectile": "decoy",
}
FLIGHT_GAP_TICKS = 4  # entity ids are reused: a gap this long between samples starts a new flight


def grenade_flights(demo: DemoData, wanted: dict[str, list[tuple[int, int]]]) -> list[dict]:
    """Flights of grenades thrown by `wanted` players inside their tick ranges ({steamid: [(first, last)]}).

    Each flight: {"kind", "thrower", "entity", "start_tick", "end_tick", "points": [(tick, (x, y, z))]};
    start is the first tick the projectile exists (out of the hand), end the last (detonation or
    landing). One full grenade query per call (slow and memory-hungry: several seconds and a few
    GB on a long demo), so call it once per demo and only when needed. An unreadable demo gives [].
    """
    if not wanted:
        return []
    try:
        frame = DemoParser(str(demo.path)).parse_grenades()
    except BaseException as exc:
        if not is_parser_error(exc):
            raise
        return []
    if frame is None or frame.empty:
        return []
    frame = frame.dropna(subset=["x", "y", "z"])
    frame = frame[frame["steamid"].astype(str).isin(set(wanted))]
    flights: list[dict] = []
    for (entity, thrower), rows in frame.groupby(["grenade_entity_id", "steamid"], sort=False):
        thrower = _steamid(thrower)
        rows = rows.sort_values("tick")
        ticks = rows["tick"].to_numpy()
        points = list(zip(ticks.tolist(), zip(rows["x"].tolist(), rows["y"].tolist(), rows["z"].tolist())))
        kinds = rows["grenade_type"].tolist()
        start = 0
        for i in range(1, len(points) + 1):
            if i < len(points) and points[i][0] - points[i - 1][0] <= FLIGHT_GAP_TICKS:
                continue
            first, last = points[start][0], points[i - 1][0]
            if any(a <= first and last <= b for a, b in wanted.get(thrower, [])):
                flights.append({
                    "kind": GRENADE_KINDS.get(str(kinds[start]), "other"),
                    "thrower": thrower,
                    "entity": int(entity),
                    "start_tick": int(first),
                    "end_tick": int(last),
                    "points": [(int(t), (float(x), float(y), float(z))) for t, (x, y, z) in points[start:i]],
                })
            start = i
    return sorted(flights, key=lambda f: f["start_tick"])


def _build_zones(engagements: list[dict]) -> list[dict]:
    """Cluster featured kills in timeline order. Do not conflate map floors."""
    groups: list[list[dict]] = []
    for event in sorted(engagements, key=lambda e: e["tick"]):
        center = event.get("engagement_center")
        if not center:
            continue
        matched = False
        for group in groups:
            group_center = _mean([e["engagement_center"] for e in group])
            if group_center and _distance(center, group_center) <= ZONE_SPLIT_DISTANCE and abs(center["z"] - group_center["z"]) <= ZONE_SPLIT_HEIGHT:
                group.append(event)
                matched = True
                break
        if not matched:
            groups.append([event])

    zones = []
    for number, group in enumerate(groups, 1):
        centers = [e["engagement_center"] for e in group]
        center = _mean(centers)
        hints = [e["place_name_hint"] for e in group if e.get("place_name_hint")]
        zones.append({
            "id": f"zone_{number}",
            "center_xyz": center,
            "radius_world_units": round(max((_distance(center, p) for p in centers), default=0.0), 2),
            "kill_ticks": [e["tick"] for e in group],
            "kill_count": len(group),
            "place_name_hint": Counter(hints).most_common(1)[0][0] if hints else None,
        })
    return zones


def build_spatial_metadata(demo: DemoData, sequences: list[Sequence]) -> dict[int, dict]:
    """Build metadata for chosen clips only; never parse every tick of a match.

    Return dict keyed by Sequence.number. A positional parse problem is reported
    in the JSON instead of breaking a working HLAE recording pipeline.
    """
    if not sequences:
        return {}

    rate = float(demo.tickrate)
    interval = max(1, round(rate / SAMPLES_PER_SECOND))
    requested_ticks: set[int] = set()
    for sequence in sequences:
        requested_ticks.update(range(sequence.start_tick, sequence.end_tick + 1, interval))
        requested_ticks.add(sequence.end_tick)
        for camera in sequence.cameras:
            requested_ticks.add(camera.tick)
        for kill in demo.kills:
            if sequence.start_tick <= kill.tick <= sequence.end_tick:
                requested_ticks.update((max(1, kill.tick - 1), kill.tick))
        for moment in sequence.moments:
            if moment.clutch:
                requested_ticks.add(moment.clutch.start_tick)

    try:
        positions, properties, note = _query_positions(demo.path, sorted(requested_ticks))
    except BaseException as error:
        if not is_parser_error(error):
            raise
        return {
            sequence.number: {
                "status": "unavailable",
                "error": f"{type(error).__name__}: {error}",
                "coordinate_system": "CS2 world coordinates",
                "primary_action_center_xyz": None,
                "primary_area_hint": None,
                "zones": [],
                "engagements": [],
                "keyframes": [],
                "pov_trajectories": [],
            }
            for sequence in sequences
        }

    spatial_by_sequence: dict[int, dict] = {}
    for sequence in sequences:
        start, end = sequence.start_tick, sequence.end_tick
        cams = sorted(sequence.cameras, key=lambda c: c.tick)
        featured = set(sequence.featured_steamids)
        active_by_tick = sorted(cams, key=lambda c: c.tick)
        selected_kills = {
            (k.tick, k.attacker_steamid, k.victim_steamid)
            for m in sequence.moments for k in m.kills
        }

        clip_kills = [k for k in demo.kills if start <= k.tick <= end]
        engagements = []
        for kill in clip_kills:
            attacker = _get_sample(positions, kill.tick, kill.attacker_steamid)
            victim = _get_sample(positions, kill.tick - 1, kill.victim_steamid, False) or _get_sample(positions, kill.tick, kill.victim_steamid)
            center = _midpoint(_point(attacker), _point(victim))
            active = next((cam.steamid for cam in reversed(active_by_tick) if cam.tick <= kill.tick), cams[0].steamid if cams else None)
            engagements.append({
                "tick": kill.tick,
                "seconds_from_clip_start": round((kill.tick - start) / rate, 6),
                "attacker_steamid": kill.attacker_steamid,
                "victim_steamid": kill.victim_steamid,
                "attacker": attacker,
                "victim": victim,
                "engagement_center": center,
                "place_name_hint": (attacker or {}).get("place_name_hint") or (victim or {}).get("place_name_hint"),
                "is_pov_kill": kill.attacker_steamid == active,
                "is_selected_moment_kill": (kill.tick, kill.attacker_steamid, kill.victim_steamid) in selected_kills,
            })

        # Determine main engagement zones using featured kills, falling back to all kills.
        featured_engagements = [e for e in engagements if e["attacker_steamid"] in featured and e["engagement_center"]]
        eligible_engagements = featured_engagements or [e for e in engagements if e["engagement_center"]]
        zones = _build_zones(eligible_engagements)
        primary_zone = max(zones, key=lambda z: z["kill_count"], default=None)

        # Map context: snapshot all players *just before* every kill, plus camera
        # switches/clutch begins. This is useful for finding viable context shots.
        interest_ticks = {max(1, k.tick - 1): "before_kill" for k in clip_kills}
        interest_ticks.update({c.tick: "camera_switch" for c in cams})
        for moment in sequence.moments:
            if moment.clutch:
                interest_ticks[moment.clutch.start_tick] = "clutch_start"
        keyframes = []
        for tick, kind in sorted(interest_ticks.items()):
            if tick < start or tick > end:
                continue
            players = []
            for sid, sample in sorted(positions.get(tick, {}).items()):
                players.append({
                    "steamid": sid,
                    "name": demo.player_names.get(sid, ""),
                    "side": demo.rosters.get(sequence.round_index, {}).get(sid),
                    **sample,
                })
            keyframes.append({
                "tick": tick,
                "seconds_from_clip_start": round((tick - start) / rate, 6),
                "kind": kind,
                "players": players,
            })

        # Short per-player trajectories instead of storing every tick of the demo.
        # Each track is usable for measuring how much ground a POV covered.
        trajectories = []
        for sid in sorted(featured):
            samples = []
            for tick in range(start, end + 1, interval):
                value = positions.get(tick, {}).get(sid)
                if value:
                    samples.append({
                        "tick": tick,
                        "seconds_from_clip_start": round((tick - start) / rate, 6),
                        **value,
                    })
            trajectories.append({"steamid": sid, "player": demo.player_names.get(sid, ""), "samples": samples})

        spatial_by_sequence[sequence.number] = {
            "status": "ok" if positions else "no_positions",
            "note": note,
            "coordinate_system": "CS2 Source 2 world units (XYZ; not 2D radar pixels)",
            "angles_unit": "degrees",
            "sample_interval_ticks": interval,
            "available_properties": properties,
            "primary_action_center_xyz": primary_zone["center_xyz"] if primary_zone else None,
            "primary_area_hint": primary_zone["place_name_hint"] if primary_zone else None,
            "primary_zone_id": primary_zone["id"] if primary_zone else None,
            "zones": zones,
            "engagements": engagements,
            "keyframes": keyframes,
            "pov_trajectories": trajectories,
            "cam_path_matches": [],
            "cam_path_note": "Import Duality Observer camera files separately to compare their map, XYZ anchors, elevation and orientation.",
        }
        spatial_by_sequence[sequence.number]["route"] = clip_route(
            sequence, spatial_by_sequence[sequence.number]
        )

    return spatial_by_sequence
