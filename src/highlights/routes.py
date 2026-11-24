"""Where a clip happens: the places the POV player moves through and where kills land.

Uses only the place hints already sampled into spatial metadata
(``pov_trajectories`` and ``engagements``); no PlaceMap or demo parsing here.
"""

from __future__ import annotations

from highlights.models import Sequence
from highlights.places import compress_route


def _active_camera(cameras: list, tick: int) -> str | None:
    """Steamid of the camera in effect at ``tick`` (cameras sorted once by tick)."""
    active = None
    for camera in cameras:
        if camera.tick <= tick:
            active = camera.steamid
        else:
            break
    return active


def clip_route(sequence: Sequence, spatial: dict) -> dict:
    """Summarise a clip's movement and kill locations from its spatial metadata.

    Returns ``{"places": [...], "text": "A → B", "kills": [...]}``; a clip with
    no sampled positions (spatial unavailable or empty) yields empty values.
    """
    cameras = sorted(sequence.cameras, key=lambda camera: camera.tick)

    names: list[str] = []
    for track in spatial.get("pov_trajectories") or []:
        steamid = track.get("steamid")
        for sample in sorted(track.get("samples") or [], key=lambda s: s["tick"]):
            if _active_camera(cameras, sample["tick"]) != steamid:
                continue
            names.append(sample.get("place_name_hint"))
    places = compress_route(names)

    kills = [
        {
            "tick": engagement["tick"],
            "seconds_from_clip_start": engagement["seconds_from_clip_start"],
            "attacker_place": (engagement.get("attacker") or {}).get("place_name_hint"),
            "victim_place": (engagement.get("victim") or {}).get("place_name_hint"),
        }
        for engagement in spatial.get("engagements") or []
        if engagement.get("is_selected_moment_kill")
    ]
    kills.sort(key=lambda kill: kill["tick"])

    return {
        "places": places,
        "text": " → ".join(places),
        "kills": kills,
    }
