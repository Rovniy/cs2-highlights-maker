"""`highlights check-ingame`: record one play in every form and write a checklist of what to look for.

Covers the assumptions the code cannot verify on its own: kill-feed filter and colours, name
replacement, extra commands, cam-path coordinates and `edit start`, held last pose, the generated
face intro, the x-ray take (`spec_show_xray 1`) and frame alignment of spliced takes. Each clip is kept raw so a problem is visible.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from highlights.config import Config
from highlights.demo.parser import DemoData
from highlights.models import Moment, Sequence
from highlights.util.log import info, warn


@dataclass
class CheckPlan:
    moment: Moment
    sequences: list[Sequence]
    pov: Sequence
    campath: Sequence | None = None
    generated: Sequence | None = None
    xray: Sequence | None = None
    notes: list[str] = field(default_factory=list)


def plan_check(demo: DemoData, config: Config, moment: Moment, campaths: list, work_dir: Path) -> CheckPlan:
    from highlights.analysis.selection import build_sequences
    from highlights.recording.cinematic import add_cinematic_sequences
    from highlights.recording.dyn_sequences import add_dynamic_sequences
    from highlights.spatial import build_spatial_metadata, sample_pov_tracks

    sequences = build_sequences([moment], demo, config.selection, config.recording, merge=False)
    spatial = build_spatial_metadata(demo, sequences)
    notes = []
    if campaths:
        from highlights.cli import _floors

        sequences, spatial = add_cinematic_sequences(
            sequences, campaths, spatial, demo.tickrate, splice=True, hold=True, floors=_floors(demo)
        )
        if len(sequences) == 1:
            notes.append("No cam path sees this play, so the cam-path checks are skipped (try another --player).")
    else:
        notes.append(f"No cam paths for {demo.map_name}, so the cam-path checks are skipped.")
    sequences, spatial = add_dynamic_sequences(
        sequences, spatial, demo, sample_pov_tracks, work_dir / "dyncams", ["face"], None
    )
    from highlights.recording.xray_cam import add_xray_sequences

    sequences, spatial = add_xray_sequences(sequences, spatial, demo, lambda *_: {}, "all")  # every kill: tests the look
    pov = sequences[0]
    campath = next((s for s in sequences if s.cinematic_of and s.dynamic_cam is None and not s.xray), None)
    xray = next((s for s in sequences if s.xray), None)
    generated = next((s for s in sequences if s.dynamic_cam is not None), None)
    if generated is None:
        notes.append("No generated face intro could be planned for this play (no position track).")
    return CheckPlan(moment, sequences, pov, campath, generated, xray, notes)


def checklist(demo: DemoData, config: Config, plan: CheckPlan, measured: float | None) -> str:
    from highlights.recording.campaths import parse_campath

    rate = demo.tickrate
    name = plan.moment.name
    kills = sorted(round((k.tick - plan.pov.start_tick) / rate, 1) for k in plan.moment.kills)
    rec = config.recording
    lines = [
        f"# In-game check — {demo.map_name}, round {plan.moment.round_index + 1}, {name}: {plan.moment.describe()}",
        "",
        "Tick a box when the clip shows it. Times are seconds from the start of that clip.",
        "",
        "## 01-pov.mp4 (first person)",
        f"- [ ] The view is {name}'s first person the whole clip.",
        f"- [ ] The kill feed shows only {name}'s kills, appearing at about {', '.join(map(str, kills))} s.",
    ]
    if measured is not None:
        lines.append(f"  - The kill-feed check measured them {measured:+.2f} s from those times; say if that looks wrong.")
    lines.append(
        "- [ ] Kill-feed rows have no dark background and no red border."
        if rec.killfeed_plain else "- [ ] Kill-feed rows keep the game's background and border (killfeed_plain is off)."
    )
    if rec.killfeed_ct_color or rec.killfeed_t_color:
        lines.append(f"- [ ] Kill-feed names use CT {rec.killfeed_ct_color or 'default'} / T {rec.killfeed_t_color or 'default'}.")
    for part, value in (
        ("background", rec.killfeed_background_color),
        ("local background", rec.killfeed_background_local_color),
        ("border", rec.killfeed_border_color),
    ):
        if value:
            lines.append(f"- [ ] Kill-feed {part} colour is {value}.")
    if rec.player_names:
        renamed = ", ".join(f"{demo.player_names.get(s, s)} → {n}" for s, n in rec.player_names.items())
        lines.append(f"- [ ] Renamed players show their new names in the kill feed: {renamed}.")
    if rec.extra_commands:
        lines.append(f"- [ ] The extra commands visibly apply: {'; '.join(rec.extra_commands)}.")
    lines += ["- [ ] Game sound is present and the CS2 music is muted.", "- [ ] The crosshair is the fixed green one.", ""]

    if plan.campath is not None:
        path = parse_campath(plan.campath.campath) if plan.campath.campath else None
        segments = ", ".join(f"{a:.1f}–{b:.1f}" for a, b in plan.campath.splice_segments or [])
        lines += [
            f"## 02-campath.mp4 (cam path {plan.campath.campath.stem})",
            "- [ ] The camera follows the cam path from the first frame (`edit start` worked).",
            f"- [ ] {name} is in frame during {segments} s. If not, cam-path coordinates do not match the demo's.",
        ]
        if path is not None and path.duration < plan.pov.duration_seconds(rate):
            lines.append(f"- [ ] From {path.duration:.1f} s the camera holds still on the path's last pose to the end.")
        lines += ["- [ ] There is no kill feed in this clip.", "- [ ] The camera never sits inside a wall or floor.", ""]
    if plan.generated is not None:
        lines += [
            "## 03-face-intro.mp4 (generated face cam)",
            f"- [ ] At 0 s the camera is in front of {name}'s face, close.",
            "- [ ] It swings round behind the head and lands exactly on the first-person view at about 3 s.",
            f"- [ ] {name}'s player model is drawn while the camera is outside it.",
            "",
        ]
    if plan.xray is not None:
        spans = ", ".join(f"{w['start_seconds']:.1f}–{w['end_seconds']:.1f}" for w in plan.xray.xray["windows"])
        lines += [
            "## 05-xray.mp4 (whole play with spec_show_xray 1)",
            "- [ ] Other players show through walls (outlines or glow). If this looks the same as 01-pov, the cvar does nothing in a",
            "      recording and `--xray` cannot work as it is: tell me what you see.",
            f"- [ ] It is still {name}'s first person, with the same HUD, and the kills land at the same times as in 01-pov.",
            f"- [ ] In 04-spliced.mp4 the x-ray dissolves in over about {plan.xray.xray['fade_seconds']:g} s before each kill and out after it"
            f" (full strength {spans} s), with no pop or jump.",
            "",
        ]
    if plan.campath is not None or plan.generated is not None or plan.xray is not None:
        lines += [
            "## 04-spliced.mp4 (what `batch` delivers)",
            "- [ ] At every cut the action continues without a jump or repeat (the takes are frame-aligned).",
            f"- [ ] Each kill still happens in first person at about {', '.join(map(str, kills))} s.",
            "",
        ]
    if plan.notes:
        lines += ["## Notes", *[f"- {note}" for note in plan.notes], ""]
    return "\n".join(lines)


def run_check(job, config: Config, moment: Moment, campaths: list, output_dir: Path) -> Path:
    from highlights import cli
    from highlights.recording.recorder import record, resolve_tools
    from highlights.video.killfeed_sync import feed_activity, measure_offset, spikes
    from highlights.video.postprocess import process_clip

    plan = plan_check(job.demo, config, moment, campaths, job.work_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    info(f"Recording {len(plan.sequences)} take(s) of round {moment.round_index + 1} ({moment.name})")
    outputs = record(job.demo, plan.sequences, config, job.work_dir)
    tools = resolve_tools(config, auto_install=False)
    recorded = [(o, process_clip(tools.ffmpeg_exe, o, job.work_dir / "clips", False)) for o in outputs]
    by_seq = {id(o.sequence): clip for o, clip in recorded}
    measured = None
    for label, sequence in (("01-pov", plan.pov), ("02-campath", plan.campath), ("03-face-intro", plan.generated), ("05-xray", plan.xray)):
        if sequence is None or id(sequence) not in by_seq:
            if sequence is not None:
                warn(f"{label}: not recorded")
            continue
        shutil.copy2(by_seq[id(sequence)], output_dir / f"{label}.mp4")
        if label == "01-pov":
            seconds = [(k.tick - sequence.start_tick) / job.demo.tickrate for k in moment.kills]
            measured, _ = measure_offset(seconds, spikes(feed_activity(tools.ffmpeg_exe, by_seq[id(sequence)])))
    if any(o.sequence.splice_segments for o, _ in recorded):
        spliced = cli._splice_cinematics(
            tools.ffmpeg_exe, recorded, job.work_dir / "clips", config.recording.crf,
            (config.recording.width, config.recording.height),
        )
        shutil.copy2(spliced[0][1], output_dir / "04-spliced.mp4")
    target = output_dir / "CHECKLIST.md"
    target.write_text(checklist(job.demo, config, plan, measured), encoding="utf-8")
    return target
