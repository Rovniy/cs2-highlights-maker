"""Write a CS2 highlight report with edit-friendly event timelines.

Drop-in replacement for src/highlights/report.py in cs2-highlights-maker.

Frame numbers and clip-relative times are *nominal estimates* based on demo ticks;
they require calibration against the rendered MP4 before frame-accurate editing.
"""

from __future__ import annotations

import json
from bisect import bisect_right
from pathlib import Path

from rich.markup import escape
from rich.table import Table

from highlights.analysis.slowmo import remap
from highlights.demo.parser import DemoData
from highlights.models import Moment, Sequence
from highlights.spatial import build_spatial_metadata
from highlights.util.log import console


_EVENT_PRIORITY = {
    "round_start": 0,
    "freeze_end": 1,
    "camera_switch": 2,
    "clutch_start": 3,
    "kill": 4,
    "clutch_end": 5,
    "round_end": 6,
}


def _timeline_events(demo: DemoData, sequence: Sequence, capture_fps: int, target_fps: int, spatial: dict | None = None) -> list[dict]:
    """Events within a selected clip, ordered by absolute demo tick."""
    rate = float(demo.tickrate)
    start, end = sequence.start_tick, sequence.end_tick
    cameras = sorted(sequence.cameras, key=lambda camera: camera.tick)
    camera_ticks = [camera.tick for camera in cameras]
    featured = set(sequence.featured_steamids)
    selected_kills = {
        (kill.tick, kill.attacker_steamid, kill.victim_steamid)
        for moment in sequence.moments
        for kill in moment.kills
    }
    events: list[dict] = []

    def add(event_type: str, tick: int, **detail: object) -> None:
        if not start <= tick <= end:
            return
        seconds = (tick - start) / rate
        events.append({
            "type": event_type,
            "tick": tick,
            "seconds_from_clip_start": round(seconds, 6),
            "capture_frame_estimate": round(seconds * capture_fps),
            "target_frame_at_normal_speed_estimate": round(seconds * target_fps),
            **detail,
        })

    for round_ in demo.rounds:
        add("round_start", round_.start_tick, round=round_.index + 1)
        add("freeze_end", round_.freeze_end_tick, round=round_.index + 1)
        add("round_end", round_.end_tick, round=round_.index + 1, winner=round_.winner)

    for camera in cameras:
        add(
            "camera_switch",
            camera.tick,
            steamid=camera.steamid,
            player=demo.player_names.get(camera.steamid, ""),
        )

    for kill in demo.kills:
        if not start <= kill.tick <= end:
            continue
        camera_index = bisect_right(camera_ticks, kill.tick) - 1
        active_camera = cameras[max(0, camera_index)].steamid if cameras else None
        add(
            "kill",
            kill.tick,
            round=kill.round_index + 1,
            attacker={"steamid": kill.attacker_steamid, "name": kill.attacker_name, "side": kill.attacker_side},
            victim={"steamid": kill.victim_steamid, "name": kill.victim_name},
            weapon=kill.weapon,
            headshot=kill.headshot,
            noscope=kill.noscope,
            penetrated=kill.penetrated,
            thrusmoke=kill.thrusmoke,
            attackerblind=kill.attackerblind,
            is_pov_kill=kill.attacker_steamid == active_camera,
            is_featured_player_kill=kill.attacker_steamid in featured,
            is_selected_moment_kill=(kill.tick, kill.attacker_steamid, kill.victim_steamid) in selected_kills,
            active_camera_steamid=active_camera,
        )

    seen_clutches: set[tuple[str, int, int]] = set()
    for moment in sequence.moments:
        clutch = moment.clutch
        if clutch is None:
            continue
        key = (clutch.steamid, clutch.start_tick, clutch.end_tick)
        if key in seen_clutches:
            continue
        seen_clutches.add(key)
        common = {
            "steamid": clutch.steamid,
            "player": clutch.name,
            "round": clutch.round_index + 1,
            "opponents": clutch.opponents,
            "won": clutch.won,
        }
        add("clutch_start", clutch.start_tick, **common)
        add("clutch_end", clutch.end_tick, **common)

    events.sort(key=lambda event: (event["tick"], _EVENT_PRIORITY.get(event["type"], 100)))
    # Put world coordinates next to kill events too, so an editor doesn't need
    # to join two arrays just to locate a specific impact.
    engagement_index = {
        (e["tick"], e["attacker_steamid"], e["victim_steamid"]): e
        for e in (spatial or {}).get("engagements", [])
    }
    for event in events:
        if event["type"] != "kill":
            continue
        key = (event["tick"], event["attacker"]["steamid"], event["victim"]["steamid"])
        position = engagement_index.get(key)
        if position:
            event["location"] = {
                "attacker": position["attacker"],
                "victim": position["victim"],
                "engagement_center_xyz": position["engagement_center"],
                "place_name_hint": position["place_name_hint"],
            }
    return events


def write_report(
    path: Path,
    demo: DemoData,
    sequences: list[Sequence],
    capture_fps: int = 60,
    target_fps: int = 60,
    spatial_by_sequence: dict[int, dict] | None = None,
    suggestions: dict[int, dict] | None = None,
) -> None:
    """Write the existing report schema plus per-sequence editing timelines.

    Called *before* recording. Timestamp-to-frame mapping is nominal: demo tick
    positions need calibration against each recorded clip's actual first frame.
    Speed ramps and transition offsets are not reflected in this report.
    """
    if capture_fps <= 0 or target_fps <= 0:
        raise ValueError("capture_fps and target_fps must be positive")

    if spatial_by_sequence is None:
        spatial_by_sequence = build_spatial_metadata(demo, sequences)

    def sequence_dict(s: Sequence) -> dict:
        return {
            "number": s.number,
            "label": s.label,
            "score": s.score,
            "round": s.round_index + 1,
            "start_tick": s.start_tick,
            "end_tick": s.end_tick,
            "duration_seconds": round(s.duration_seconds(demo.tickrate), 1),
            "clip_filename": f"clip_{s.number:02d}.mp4",
            "slowmo": s.slowmo,
            "tilt": s.tilt,
            "xray": ({**s.xray, "of_sequence": s.cinematic_of} if s.xray else None),
            "cinematic": (
                {
                    "campath": s.campath.name,
                    "of_sequence": s.cinematic_of,
                    "candidates": s.cinematic_candidates,
                    "generated_cams": s.dynamic_cam,
                    "static_cam": s.static_cam,
                    "static_downtime": s.static_downtime,
                    "campath_retime": s.campath_retime,
                    "splice_segments": (
                        [{"start_seconds": a, "end_seconds": b} for a, b in s.splice_segments]
                        if s.splice_segments is not None
                        else None
                    ),
                }
                if s.campath is not None
                else None
            ),
            "cameras": [
                {"tick": c.tick, "steamid": c.steamid, "name": demo.player_names.get(c.steamid, "")}
                for c in s.cameras
            ],
            "moments": [
                {
                    "player": m.name,
                    "steamid": m.steamid,
                    "kills": m.kill_count,
                    "kill_ticks": [kill.tick for kill in m.kills],
                    "description": m.describe(),
                    "score": m.score,
                    "clutch": (
                        {
                            "opponents": m.clutch.opponents,
                            "won": m.clutch.won,
                            "kills": m.clutch.kills,
                            "start_tick": m.clutch.start_tick,
                            "end_tick": m.clutch.end_tick,
                        }
                        if m.clutch
                        else None
                    ),
                }
                for m in s.moments
            ],
            "events": _timeline_events(demo, s, capture_fps, target_fps, spatial_by_sequence.get(s.number)),
            "spatial": spatial_by_sequence.get(s.number, {"status": "unavailable"}),
        }

    data = {
        "schema_version": 3,
        "demo": str(demo.path),
        "map": demo.map_name,
        "tickrate": demo.tickrate,
        "rounds": len(demo.rounds),
        "editing": {
            "capture_fps": capture_fps,
            "target_output_fps": target_fps,
            "timing_status": "nominal_unverified",
            "time_origin": "each sequence's start_tick",
            "note": (
                "Frame indices are estimates, not measured MP4 frame positions. "
                "Calibrate capture start and use ffprobe for actual clip durations. "
                "Recalculate timestamps after retiming, speed ramps or transitions. "
                "target_output_fps is an editing goal, not the FPS currently exported by this command."
            ),
        },
        "sequences": [],
    }
    for s in sequences:
        record = sequence_dict(s)
        if suggestions is not None:
            record["cinematic_suggestion"] = suggestions.get(s.number)
        data["sequences"].append(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def finalize_report(
    path: Path,
    clip_durations: dict[int, float],
    overlap_seconds: float = 0.0,
    offset_seconds: float = 0.0,
) -> None:
    """Add final-video timing to a report written before recording.

    clip_durations maps sequence number -> ffprobe duration of its processed clip;
    only recorded sequences appear, in report order. overlap_seconds is the
    crossfade length (0 for stream-copy concat). offset_seconds is a calibration
    shift (measured capture start vs. start_tick) applied to every kill time.
    Adds video_start_seconds / video_time_seconds to sequences and kill events,
    and a flat top-level beat_targets list of the POV kills, sorted by video time.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    position = 0.0
    beat_targets: list[dict] = []
    for sequence in data["sequences"]:
        duration = clip_durations.get(sequence["number"])
        if duration is None:
            continue
        sequence["clip_duration_seconds"] = round(duration, 3)
        sequence["video_start_seconds"] = round(position, 3)
        kills = [e for e in sequence["events"] if e["type"] == "kill"]
        pov_kills = [e for e in kills if e["is_pov_kill"] and e["is_selected_moment_kill"]]
        slowmo = sequence.get("slowmo")
        for event in kills:
            clip_time = event["seconds_from_clip_start"]
            if slowmo:
                clip_time = remap(clip_time, slowmo["start_seconds"], slowmo["end_seconds"], slowmo["speed"])
            event["video_time_seconds"] = round(position + clip_time + offset_seconds, 3)
        for index, event in enumerate(pov_kills, 1):
            beat_targets.append({
                "video_time_seconds": event["video_time_seconds"],
                "sequence": sequence["number"],
                "cinematic": bool(sequence.get("cinematic")),
                "clip_time_seconds": event["seconds_from_clip_start"],
                "tick": event["tick"],
                "kill_index": index,
                "kills_in_clip": len(pov_kills),
                "weapon": event["weapon"],
                "headshot": event["headshot"],
                "special": bool(
                    event["noscope"] or event["thrusmoke"] or event["attackerblind"] or event["penetrated"]
                ),
                "victim": event["victim"]["name"],
            })
        position += duration - overlap_seconds

    beat_targets.sort(key=lambda target: target["video_time_seconds"])
    data["beat_targets"] = beat_targets
    data["editing"]["video_timing"] = {
        "status": "from_clip_durations",
        "overlap_seconds": overlap_seconds,
        "offset_seconds": offset_seconds,
        "note": "video_time_seconds = sum of earlier clip lengths + clip time + offset. "
        "Still assumes capture starts at start_tick unless offset_seconds was calibrated.",
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def print_sequences_table(
    demo: DemoData,
    sequences: list[Sequence],
    spatial_by_sequence: dict[int, dict] | None = None,
) -> None:
    table = Table(title=f"Highlights — {demo.map_name} ({len(demo.rounds)} rounds)")
    table.add_column("#", justify="right")
    table.add_column("Round", justify="right")
    table.add_column("Highlight")
    show_route = spatial_by_sequence is not None and any(
        spatial_by_sequence.get(s.number, {}).get("route", {}).get("text") for s in sequences
    )
    if show_route:
        table.add_column("Where")
    table.add_column("Score")
    table.add_column("Length")
    show_slowmo = any(s.slowmo for s in sequences)
    if show_slowmo:
        table.add_column("Slow-mo")
    table.add_column("Ticks", justify="right")
    for s in sequences:
        row = [
            str(s.number),
            str(s.round_index + 1),
            s.label,
        ]
        if show_route:
            row.append(spatial_by_sequence.get(s.number, {}).get("route", {}).get("text", ""))
        row += [
            f"{s.score:.1f}",
            f"{s.duration_seconds(demo.tickrate):.0f}s",
        ]
        if show_slowmo:
            row.append(f"{s.slowmo['reason']} → {s.slowmo['victim']}" if s.slowmo else "")
        row.append(f"{s.start_tick}-{s.end_tick}")
        table.add_row(*row)
    console.print(table)


def print_suggestions_table(demo: DemoData, sequences: list[Sequence], suggestions: dict[int, dict]) -> None:
    """Per clip: whether a cinematic is worth it and which cam path would work."""
    table = Table(title=f"Cinematic suggestions — {demo.map_name}")
    table.add_column("#", justify="right")
    table.add_column("Highlight")
    table.add_column("Fit")
    table.add_column("Cam path")
    table.add_column("Path route")
    table.add_column("Why")
    styles = {"good": "green", "possible": "yellow", "none": "dim"}
    for s in sequences:
        suggestion = suggestions.get(s.number)
        if suggestion is None:
            continue
        best = suggestion.get("best")
        if best:
            lines = [escape(best["name"]) + (f' "{escape(best["label"])}"' if best.get("label") else "")]
            cover = best.get("coverage_seconds")
            lines.append(f"sees player {cover:.1f}s" if cover is not None else "sees player -")
            alternatives = suggestion.get("alternatives") or []
            if alternatives:
                names = ", ".join(
                    escape(a["name"]) + (f" {a['coverage_seconds']:.1f}s" if a.get("coverage_seconds") is not None else "")
                    for a in alternatives
                )
                lines.append(f"also: {names}")
            path_cell = "\n".join(lines)
            route_cell = escape(" > ".join(best.get("route") or [])) or "-"
        else:
            path_cell = "-"
            route_cell = "-"
        verdict = suggestion.get("verdict", "")
        why_cell = escape("; ".join(suggestion.get("why") or []))
        style = styles.get(verdict)
        fit_cell = f"[{style}]{escape(verdict)}[/]" if style else escape(verdict)
        table.add_row(
            str(s.number),
            escape(s.label),
            fit_cell,
            path_cell,
            route_cell,
            why_cell,
        )
    console.print(table)


def minutes_seconds(seconds: float) -> str:
    """`4m 05s` (rounded to whole seconds)."""
    minutes, rest = divmod(round(seconds), 60)
    return f"{minutes}m {rest:02d}s"


def print_top_table(
    rows: list[tuple[DemoData, Moment]],
    slowmo: list[str] | None = None,
    clips: list[str] | None = None,
    labels: list[str] | None = None,
    numbers: list[int] | None = None,
    matches: list[str] | None = None,
    lengths: list[float] | None = None,
) -> None:
    demo_count = len({str(demo.path) for demo, _ in rows})
    table = Table(title=f"Top highlights across {demo_count} demos")
    table.add_column("#", justify="right")
    table.add_column("Demo")
    table.add_column("Map")
    table.add_column("Round", justify="right")
    table.add_column("Player")
    table.add_column("Highlight")
    table.add_column("Score")
    if lengths is not None:
        table.add_column("Length", justify="right")
    if slowmo is not None:
        table.add_column("Slow-mo")
    if clips is not None:
        table.add_column("Clip")
    if matches is not None:
        table.add_column("Match")
    for index, (demo, moment) in enumerate(rows, 1):
        name = labels[index - 1] if labels is not None else demo.path.stem
        if len(name) > 24:
            name = name[:23] + "…"
        table.add_row(
            str(numbers[index - 1] if numbers is not None else index),
            name,
            demo.map_name,
            str(moment.round_index + 1),
            moment.name,
            " · ".join([moment.describe(), *moment.tags]),
            f"{moment.score:.1f}",
            *([f"{lengths[index - 1]:.0f}s"] if lengths is not None else []),
            *([slowmo[index - 1]] if slowmo is not None else []),
            *([clips[index - 1]] if clips is not None else []),
            *([matches[index - 1]] if matches is not None else []),
        )
    console.print(table)
