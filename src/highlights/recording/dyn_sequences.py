"""Add generated-cam takes (--intro-cam, --downtime-cam) next to the POV sequences."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from highlights.demo.parser import DemoData
from highlights.models import Sequence
from highlights.recording.cinematic import _kill_times
from highlights.recording.dyncams import (
    Segment,
    Track,
    build_keyframes,
    eye_pose_at,
    pick_style,
    plan_segments,
    write_campath,
)
from highlights.util.log import info, warn

Sampler = Callable[[DemoData, dict], dict]


def _switch_times(sequence: Sequence, tickrate: float) -> list[float]:
    return sorted(
        (c.tick - sequence.start_tick) / tickrate for c in sequence.cameras if c.tick > sequence.start_tick
    )


def _eye_source(sequence: Sequence, tracks: dict[str, Track], tickrate: float):
    """eye_at(t): the first-person pose of whoever the POV camera is on at clip time t."""
    cameras = sorted(sequence.cameras, key=lambda c: c.tick)

    def eye_at(t: float):
        tick = sequence.start_tick + t * tickrate
        active = next((c.steamid for c in reversed(cameras) if c.tick <= tick + 1e-6), None)
        return eye_pose_at(tracks.get(active, []), t) if active else None

    return eye_at


def add_dynamic_sequences(
    sequences: list[Sequence],
    spatial_by_sequence: dict[int, dict],
    demo: DemoData,
    sample: Sampler,
    out_dir: Path,
    intro: list[str] | None,
    downtime: list[str] | None,
    intro_transition: str = "swoop",
) -> tuple[list[Sequence], dict[int, dict]]:
    """Follow each POV sequence (and its real cinematic takes) with one generated-cam
    take whose splice segments are the opener and/or down-time cuts.

    Down-time cuts avoid ranges a real splice-mode cam path already covers. The take
    is recorded like any cinematic and swapped into the POV clip by the splice step.
    Plays with no segment get no take. Returns the list renumbered 1..N and the
    spatial data re-keyed (generated takes share their POV's spatial data).
    """
    tickrate = float(demo.tickrate)
    plans: dict[int, list[Segment]] = {}
    for sequence in sequences:
        if sequence.cinematic_of is not None:
            continue
        taken = [
            seg
            for other in sequences
            if other.cinematic_of == sequence.number and other.splice_segments
            for seg in other.splice_segments
        ]
        duration = sequence.duration_seconds(tickrate)
        base = plan_segments(
            duration,
            _kill_times(sequence, tickrate),
            _switch_times(sequence, tickrate),
            "face" if intro else None,
            "face" if downtime else None,
            taken,
        )
        # One style per kind per clip, so a clip never mixes cam styles.
        styles = {
            "intro": pick_style(intro, sequence.start_tick) if intro else "",
            "downtime": pick_style(downtime, sequence.start_tick + 1) if downtime else "",
        }
        segments = [replace(seg, style=styles[seg.kind], swoop=intro_transition == "swoop") for seg in base]
        if segments:
            plans[sequence.number] = segments

    tracks = (
        sample(demo, {n: (next(s for s in sequences if s.number == n), [(g.start, g.end) for g in segs]) for n, segs in plans.items()})
        if plans
        else {}
    )

    new_for: dict[int, Sequence] = {}
    for number, segments in plans.items():
        pov = next(s for s in sequences if s.number == number)
        eye_at = _eye_source(pov, tracks.get(number, {}), tickrate)
        try:
            keys = build_keyframes(segments, eye_at)
        except ValueError as exc:
            warn(f"{pov.label}: no generated cam ({exc})")
            continue
        file = write_campath(out_dir / f"dyncam_{pov.start_tick}.campath", keys)
        summary = ", ".join(f"{s.style} {s.kind}" for s in segments)
        info(f"{pov.label}: generated cams — {summary} ({sum(s.end - s.start for s in segments):.1f}s)")
        new_for[number] = replace(
            pov,
            label=f"{pov.label} — generated cams",
            campath=file,
            cinematic_of=number,  # remapped below
            splice_segments=[(s.start, s.end) for s in segments],
            dynamic_cam=[
                {
                    "start_seconds": s.start,
                    "end_seconds": s.end,
                    "kind": s.kind,
                    "style": s.style,
                    **({"transition": "swoop" if s.swoop else "cut"} if s.kind == "intro" else {}),
                }
                for s in segments
            ],
            cinematic_candidates=None,
        )

    return insert_takes(sequences, spatial_by_sequence, new_for)


def insert_takes(
    sequences: list[Sequence], spatial_by_sequence: dict[int, dict], new_for: dict[int, Sequence | list[Sequence]]
) -> tuple[list[Sequence], dict[int, dict]]:
    """Put each new take (or list of takes, keyed by its POV's number) after that POV's existing
    takes, renumber everything 1..N and re-key the spatial data (new takes share their POV's)."""
    new_for = {n: (t if isinstance(t, list) else [t]) for n, t in new_for.items()}
    result: list[Sequence] = []
    spatial: dict[int, dict] = {}
    new_number: dict[int, int] = {}
    pending: list[tuple[Sequence, int | None]] = []  # (sequence, old number to take spatial from)
    last_group: int | None = None

    def flush_dynamic(of: int | None) -> None:
        if of is not None and of in new_for:
            pending.extend((take, of) for take in new_for[of])

    for sequence in sequences:
        if sequence.cinematic_of is None:
            flush_dynamic(last_group)
            last_group = sequence.number
        pending.append((sequence, sequence.number))
    flush_dynamic(last_group)

    dynamic_ids = {id(take) for takes in new_for.values() for take in takes}
    for sequence, source in pending:
        number = len(result) + 1
        if id(sequence) not in dynamic_ids:
            new_number[sequence.number] = number
        result.append(replace(sequence, number=number))
        if source in spatial_by_sequence:
            spatial[number] = spatial_by_sequence[source]
    # Every take points at its POV sequence's new number.
    result = [
        replace(s, cinematic_of=new_number[s.cinematic_of]) if s.cinematic_of is not None else s for s in result
    ]
    return result, spatial
