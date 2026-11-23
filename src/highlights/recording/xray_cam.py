"""`--xray auto|all`: a second take of the play with `spec_show_xray 1`, dissolved over the POV clip.

The whole play is recorded again with x-ray on (frame-aligned with the POV take, like a cinematic
take); the splice step then fades it in and out around the kills that earn it (`analysis.xray`).
`auto` only makes a take for plays that have a wallbang or through-smoke kill; `all` windows every
selected kill of every play. The take is spliced under any cam-path or generated-cam cuts, so those
win where they overlap. Whether the cvar shows outlines in the recording is unvalidated (see
`highlights check-ingame`).
"""

from __future__ import annotations

from dataclasses import replace

from highlights.analysis.xray import FADE, Sampler, classify_kills, kill_key, plan_windows
from highlights.demo.parser import DemoData
from highlights.models import Sequence
from highlights.util.log import info


def add_xray_sequences(
    sequences: list[Sequence],
    spatial_by_sequence: dict[int, dict],
    demo: DemoData,
    sample: Sampler,
    mode: str = "auto",
    fade: float = FADE,
) -> tuple[list[Sequence], dict[int, dict]]:
    """One x-ray take per POV play that has something to show (see module doc); the rest untouched."""
    from highlights.recording.dyn_sequences import insert_takes

    if mode == "off":
        return sequences, spatial_by_sequence
    povs = [s for s in sequences if s.cinematic_of is None and s.moments]
    reasons = classify_kills(povs, demo, sample) if mode == "auto" else {}
    rate = float(demo.tickrate)
    new_for: dict[int, Sequence] = {}
    for pov in povs:
        duration = pov.duration_seconds(rate)
        shots: dict[int, tuple[float, int, tuple[str, ...]]] = {}
        for moment in pov.moments:
            for kill in moment.kills:
                if not pov.start_tick <= kill.tick <= pov.end_tick:
                    continue
                why = reasons.get(kill_key(kill), ()) if mode == "auto" else ("kill",)
                if why:
                    shots[kill.tick] = ((kill.tick - pov.start_tick) / rate, kill.tick, why)
        windows = plan_windows(list(shots.values()), duration, fade=fade)
        if not windows:
            continue
        info(
            f"{pov.label}: x-ray — "
            + ", ".join(f"{', '.join(w.reasons)} at {w.start:.1f}-{w.end:.1f}s" for w in windows)
        )
        new_for[pov.number] = replace(
            pov,
            label=f"{pov.label} — x-ray",
            cinematic_of=pov.number,  # remapped by insert_takes
            splice_segments=[(w.start, w.end) for w in windows],
            xray={
                "mode": mode,
                "fade_seconds": fade,
                "windows": [
                    {
                        "start_seconds": w.start,
                        "end_seconds": w.end,
                        "fade_in": w.fade_in,
                        "fade_out": w.fade_out,
                        "kill_ticks": list(w.kills),
                        "reasons": list(w.reasons),
                    }
                    for w in windows
                ],
            },
            campath=None,
            campath_retime=None,
            cinematic_candidates=None,
            dynamic_cam=None,
            static_cam=None,
            static_downtime=None,
            slowmo=None,
            tilt=None,
        )
    if not new_for:
        return sequences, spatial_by_sequence
    return insert_takes(sequences, spatial_by_sequence, new_for)
