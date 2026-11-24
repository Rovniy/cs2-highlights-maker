"""Suggest which clips deserve a cinematic take and which cam path would work.

Runs the splice matching once quietly and judges each clip from its moment
scores, how compact the kills are, and how well the chosen path sees the player.
"""

from __future__ import annotations

from math import dist

from highlights.floors import Floors
from highlights.models import Sequence
from highlights.places import PlaceMap
from highlights.recording.campaths import CamPath
from highlights.recording.cinematic import MIN_COVERAGE, add_cinematic_sequences

WORTHY_SCORE = 6.0  # a clip with at least this moment score counts as strong
STRONG_KILLS = 3  # ... or this many kills
MAX_ACTION_SPREAD = 1500.0  # world units: farthest two selected kills (horizontal)
MAX_FLOOR_SPREAD = 200.0  # world units: selected kills on one floor (height spread)
GOOD_COVERAGE = 6.0  # seconds a path must see the player to be a good suggestion
ALTERNATIVES = 2


def _strength(sequence: Sequence) -> str | None:
    """Reason the clip is worth a cinematic, from its strongest fact; None when weak."""
    kills = sum(m.kill_count for m in sequence.moments)
    if kills >= STRONG_KILLS:
        return f"{kills}K"
    clutch = next((m.clutch for m in sequence.moments if m.clutch is not None and m.clutch.won), None)
    if clutch is not None:
        return f"1v{clutch.opponents} clutch"
    if any(k.is_special for m in sequence.moments for k in m.kills):
        return "special kill"
    if sequence.score >= WORTHY_SCORE:
        return f"score {sequence.score:g}"
    return None


def _spread(centers: list[tuple[float, float, float]]) -> tuple[float, float]:
    """(farthest two centers horizontally, height spread)."""
    spread = max((dist(a[:2], b[:2]) for a in centers for b in centers), default=0.0)
    heights = max(c[2] for c in centers) - min(c[2] for c in centers)
    return spread, heights


def _compactness(spatial: dict) -> tuple[bool, str | None]:
    """Are the selected kills close together and on one floor, plus why not."""
    centers = [
        (c["x"], c["y"], c["z"])
        for e in spatial.get("engagements", [])
        if e.get("is_selected_moment_kill") and e.get("engagement_center")
        for c in [e["engagement_center"]]
    ]
    if not centers:
        return True, None  # nothing to locate: treat as compact
    spread, heights = _spread(centers)
    if spread > MAX_ACTION_SPREAD:
        return False, f"kills span {spread:.0f} units"
    if heights > MAX_FLOOR_SPREAD:
        return False, "kills on different floors"
    return True, None


def _view(candidate: dict, clip_places: list[str], chosen_name: str | None) -> dict:
    """The suggestion keys for one candidate, with its overlap with the clip's places."""
    path_places = set(candidate.get("route") or [])
    clip = set(clip_places)
    overlap = len(clip & path_places) / len(clip) if clip and path_places else 0.0
    return {
        "name": candidate["name"],
        "label": candidate.get("label"),
        "distance": candidate["distance"],
        "coverage_seconds": candidate["coverage_seconds"],
        "route": list(candidate.get("route") or []),
        "route_overlap": round(overlap, 2),
        "_chosen": candidate["name"] == chosen_name,
    }


def _suggest(
    sequence: Sequence,
    spatial: dict | None,
    candidates: list[dict],
    chosen_name: str | None,
) -> dict:
    """One clip's verdict, reasons and path suggestion."""
    if spatial is None:
        return {"verdict": "none", "why": ["no position data"], "clip_route": "", "best": None, "alternatives": []}
    why: list[str] = []
    strong = _strength(sequence)
    if strong:
        why.append(strong)
    compact, reason = _compactness(spatial)
    if reason:
        why.append(reason)
    route = spatial.get("route") or {}
    clip_places = list(route.get("places") or [])
    views = [_view(c, clip_places, chosen_name) for c in candidates]
    best = next((v for v in views if v["_chosen"]), None)
    alternatives = [v for v in views if not v["_chosen"]][:ALTERNATIVES]
    if best:
        if best["route_overlap"] > 0:
            shared = ", ".join(sorted(set(clip_places) & set(best["route"])))
            why.append(f"path passes {shared}")
    else:
        why.append(f"no cam path sees the player for {MIN_COVERAGE:g}s of down time")
    for view in (*([best] if best else []), *alternatives):
        view.pop("_chosen")
    if best and strong and compact and best["coverage_seconds"] >= GOOD_COVERAGE:
        verdict = "good"
    elif best and (strong or compact):
        verdict = "possible"
    else:
        verdict = "none"
    return {
        "verdict": verdict,
        "why": why,
        "clip_route": route.get("text", ""),
        "best": best,
        "alternatives": alternatives,
    }


def _path_name(sequence: Sequence) -> str | None:
    """Name of the cam path a cinematic sequence was cut from."""
    return sequence.campath.stem if sequence.campath else None


def suggest_cinematics(
    sequences: list[Sequence],
    campaths: list[CamPath],
    spatial_by_sequence: dict[int, dict],
    tickrate: float,
    place_map: PlaceMap | None = None,
    labels: dict[str, str] | None = None,
    forced: CamPath | None = None,
    floors: Floors | None = None,
) -> dict[int, dict]:
    """Per input sequence number: does this clip deserve a cinematic, and which path."""
    result, _ = add_cinematic_sequences(
        sequences,
        campaths,
        spatial_by_sequence,
        tickrate,
        takes=1,
        splice=True,
        forced=forced,
        place_map=place_map,
        labels=labels,
        quiet=True,
        floors=floors,
    )
    # add_cinematic_sequences renumbers: a POV sequence is followed by its cinematic
    # (if any), so walk the result in order and pair each POV with the next input.
    suggestions: dict[int, dict] = {}
    inputs = iter(sequences)
    source: Sequence | None = None
    pov_number: int | None = None
    for sequence in result:
        if sequence.cinematic_of is None:
            source, pov_number = next(inputs, None), sequence.number
            continue
        if source is None or sequence.cinematic_of != pov_number or not sequence.campath:
            continue
        candidates = list(sequence.cinematic_candidates or [])
        suggestions[source.number] = _suggest(
            source, spatial_by_sequence.get(source.number), candidates, _path_name(sequence)
        )
    for sequence in sequences:
        if sequence.number not in suggestions:
            suggestions[sequence.number] = _suggest(sequence, spatial_by_sequence.get(sequence.number), [], None)
    return suggestions
