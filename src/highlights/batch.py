"""`highlights batch`: one finished clip + JSON report per highlight, across several demos.

The best N moments of the (optionally player-filtered) pool of all demos each become their own
file, `<label>-<player>-<n>.mp4` + `.json`, shaped like a single-clip `run` so a separate
compilation script can join them. Selection is global; recording is one game session per demo.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, replace
from pathlib import Path

from highlights.analysis.xray import FADE as XRAY_FADE
from highlights.analysis.selection import (
    apply_picks,
    build_sequences,
    clip_seconds,
    eligible_moments,
    pick_rows,
    round_allowed,
)
from highlights.config import Config
from highlights.demo.parser import DemoData
from highlights.demo.players import PlayerNotFound, resolve_player
from highlights.models import Moment, Sequence
from highlights.util.log import info, warn


def parse_demo_arg(value: str) -> tuple[str, Path]:
    """`LABEL=PATH` or just `PATH` (the label is then the file name without extension)."""
    label, sep, raw = value.partition("=")
    if sep and label.strip() and raw.strip():
        return label.strip(), Path(raw.strip())
    return Path(value).stem, Path(value)


def slugify(name: str, fallback: str = "player") -> str:
    """Lowercase letters, digits and underscores only ("deviN-" -> "devin")."""
    return re.sub(r"[^a-z0-9_]", "", name.lower()) or fallback


def team_steamids(names_file: Path, teams: list[str]) -> set[str]:
    """SteamID64s whose `team` in a cotw-style names.json is one of `teams` (case-insensitive)."""
    wanted = {team.lower() for team in teams}
    data = json.loads(names_file.read_text(encoding="utf-8"))
    return {
        key
        for key, value in data.items()
        if key.isdigit() and isinstance(value, dict) and str(value.get("team", "")).lower() in wanted
    }


def present_players(demo: DemoData, queries: list[str], team_ids: set[str]) -> tuple[set[str], list[str]]:
    """The filter's players that played in this demo, and the queries that matched nobody.

    Unlike `run`/`top`, absence is normal here (a team plays in only some of the demos)."""
    found = {sid for sid in team_ids if sid in demo.player_names}
    missing: list[str] = []
    for query in (q.strip() for item in queries for q in item.split(",")):
        if not query:
            continue
        try:
            found.add(resolve_player(demo, query))
        except PlayerNotFound as exc:
            if "several" in str(exc):
                warn(f"{demo.path.name}: {exc} — skipping that name")
            missing.append(query)
    return found, missing


@dataclass
class DemoJob:
    label: str
    path: Path
    demo: DemoData
    work_dir: Path


def clip_names(rows: list[tuple[str, Moment]]) -> list[str]:
    """`<label>-<player>-<n>` for each (label, moment) in rank order; n counts per label and player."""
    counts: dict[tuple[str, str], int] = {}
    names = []
    for label, moment in rows:
        key = (label, slugify(moment.name, moment.steamid))
        counts[key] = counts.get(key, 0) + 1
        names.append(f"{key[0]}-{key[1]}-{counts[key]}")
    return names


def rank_block(rank: int, picked: list[Moment]) -> dict:
    """The clip's place in the whole batch: `rank` 1 = best, its score and the score relative to the best."""
    best = max((m.score for m in picked), default=0.0) or 1.0
    score = picked[rank - 1].score
    return {"rank": rank, "of": len(picked), "score": round(score, 2), "relative_score": round(score / best, 3)}


def clip_group(sequences: list[Sequence], spatial: dict[int, dict], pov: Sequence) -> tuple[list[Sequence], dict[int, dict]]:
    """A POV sequence and its cinematic takes, renumbered 1..k, with their spatial data."""
    group = [pov, *(s for s in sequences if s.cinematic_of == pov.number)]
    numbering = {s.number: i for i, s in enumerate(group, 1)}
    renumbered = [
        replace(s, number=numbering[s.number], cinematic_of=numbering.get(s.cinematic_of) if s.cinematic_of else None)
        for s in group
    ]
    return renumbered, {numbering[s.number]: spatial[s.number] for s in group if s.number in spatial}


@dataclass
class ClipPlan:
    name: str
    pov: Sequence  # the POV sequence as numbered in the demo's recording
    group: list[Sequence]  # POV + cinematic takes, numbered 1..k (what the clip's JSON describes)
    spatial: dict[int, dict]


def plan_demo(
    job: DemoJob,
    picked: list[tuple[Moment, str]],
    config: Config,
    cinematic: bool,
    cinematic_path: str | None,
    hold: bool,
    align: bool,
    intro_cam: list[str] | None,
    downtime_cam: list[str] | None,
    tilt_mode: str = "off",
    grenade_cam: str = "off",
    intro_transition: str = "swoop",
    protect_kills: bool = True,
    static_cam: str | list[str] | None = None,
    static_downtime: str | None = None,
    xray: str = "off",
    xray_fade: float = XRAY_FADE,
) -> tuple[list[Sequence], list[ClipPlan]]:
    """Every sequence to record for this demo (POV plus cinematic/generated takes) and the clips they make."""
    from highlights import cli
    from highlights.spatial import build_spatial_metadata, sample_pov_tracks

    demo = job.demo
    sequences = build_sequences([m for m, _ in picked], demo, config.selection, config.recording, merge=False)
    spatial = build_spatial_metadata(demo, sequences)
    names = {id(m): name for m, name in picked}
    floors = cli._floors(demo) if cinematic or static_cam or static_downtime else None

    if cinematic:
        from highlights.recording.cinematic import add_cinematic_sequences

        try:
            campaths, place_map, labels = cli._load_cinematic_inputs(config, demo.map_name)
            forced = None
            if cinematic_path:
                forced = next((p for p in campaths if p.name.lower() == cinematic_path.lower()), None)
                if forced is None:
                    warn(f"{job.label}: no cam path named {cinematic_path!r} for {demo.map_name}, no cinematic")
            if forced is not None or not cinematic_path:
                sequences, spatial = add_cinematic_sequences(
                    sequences,
                    campaths,
                    spatial,
                    demo.tickrate,
                    splice=True,
                    forced=forced,
                    place_map=place_map,
                    labels=labels,
                    hold=hold or align,
                    align=align,
                    protect_kills=protect_kills,
                    floors=floors,
                )
        except Exception as exc:  # a map without cam paths must not stop the batch
            warn(f"{job.label}: no cinematic ({exc})")

    if intro_cam or downtime_cam:
        from highlights.recording.dyn_sequences import add_dynamic_sequences

        sequences, spatial = add_dynamic_sequences(
            sequences, spatial, demo, sample_pov_tracks, job.work_dir / "dyncams", intro_cam, downtime_cam,
            intro_transition,
        )

    if static_downtime:
        sequences, spatial = cli._add_static_downtime(
            sequences, spatial, demo, config, static_downtime, job.work_dir, floors
        )

    if grenade_cam != "off":
        from highlights.recording.grenade_cam import add_grenade_sequences
        from highlights.spatial import grenade_flights

        sequences, spatial = add_grenade_sequences(
            sequences, spatial, demo, grenade_flights, job.work_dir / "dyncams", grenade_cam
        )

    if xray != "off":
        sequences, spatial = cli._add_xray(sequences, spatial, demo, xray, xray_fade)

    if tilt_mode != "off":
        from highlights.analysis.tilt import plan_sequences as plan_tilt

        sequences = plan_tilt(sequences, spatial, demo.tickrate, tilt_mode)

    if static_cam:
        sequences, spatial = cli._add_static_cams(sequences, spatial, demo, config, static_cam, job.work_dir, floors)

    clips = []
    for pov in (s for s in sequences if s.cinematic_of is None):
        group, group_spatial = clip_group(sequences, spatial, pov)
        clips.append(ClipPlan(names[id(pov.moments[0])], pov, group, group_spatial))
    return sequences, clips


def slowmo_hint(pick, pov: Sequence, tickrate: float) -> dict | None:
    """The suggested slow-motion beat of a clip, for the compilation step (never applied here)."""
    from highlights.analysis.slowmo import SPEED, window

    if pick is None:
        return None
    kill_seconds = (pick.kill.tick - pov.start_tick) / tickrate
    start, end = window(kill_seconds, pov.duration_seconds(tickrate))
    return {
        "kill_tick": pick.kill.tick,
        "clip_time_seconds": round(kill_seconds, 3),
        "start_seconds": start,
        "end_seconds": end,
        "speed": SPEED,
        "worth": pick.worth,
        "reason": pick.reason,
        "victim": pick.kill.victim_name,
        "applied": False,
    }


def run_batch(
    jobs: list[DemoJob],
    config: Config,
    queries: list[str],
    team_ids: set[str],
    output_dir: Path,
    json_only: bool = False,
    keep_clips: bool = False,
    timing_offset: float = 0.0,
    cinematic: bool = False,
    cinematic_path: str | None = None,
    hold: bool = False,
    align: bool = False,
    intro_cam: list[str] | None = None,
    downtime_cam: list[str] | None = None,
    tilt_mode: str = "off",
    round_rules: list | None = None,
    pick: list[int] | None = None,
    exclude: list[int] | None = None,
    resume: bool = False,
    reuse_recordings: bool = False,
    buildup: str | None = None,
    buildup_length: float = 6.0,
    review: bool = True,
    calibrate: bool = True,
    grenade_cam: str = "off",
    intro_transition: str = "swoop",
    static_cam: str | list[str] | None = None,
    static_downtime: str | None = None,
    cinematic_only: bool = False,
    speed: float = 1.0,
    clip_length: float = 5.0,
    output_fps: int | None = None,
    xray: str = "off",
    xray_fade: float = XRAY_FADE,
    kill_filter=None,
) -> list[Path]:
    """Select the best moments across all demos, then record each demo and write one clip + JSON per moment."""
    import click

    from highlights import cli, faceit
    from highlights.report import print_top_table, write_report

    filtered = bool(queries or team_ids)
    rows: list[tuple[DemoData, Moment]] = []
    label_of: dict[int, str] = {}
    for job in jobs:
        label_of[id(job.demo)] = job.label
        moments = cli._scored_moments(job.demo, config)
        if kill_filter is not None:
            from highlights.analysis.killfilter import apply_kill_filter

            moments = apply_kill_filter(moments, job.demo, kill_filter)
        steamids = None
        if filtered:
            steamids, missing = present_players(job.demo, queries, team_ids)
            if not steamids:
                warn(f"{job.label}: none of the players are in this demo, skipping it")
                continue
            if missing:
                info(f"{job.label}: not in this demo: {', '.join(missing)}")
        rows.extend(
            (job.demo, m)
            for m in eligible_moments(moments, config.selection, steamids)
            if round_allowed(round_rules or [], job.label, m.round_index)
        )
    if not rows:
        hint = cli._no_kill_match(kill_filter, [job.demo for job in jobs]) if kill_filter is not None else ""
        raise click.ClickException(f"No highlights matched the criteria — try --min-kills 1 or check the demos{hint}")

    picked, total = pick_rows(rows, config.selection, config.recording)
    if not picked:
        raise click.ClickException("No clip fits in --max-duration — raise it")
    numbers = None
    if pick or exclude:
        try:
            kept = apply_picks(picked, pick or [], exclude or [])
        except ValueError as exc:
            raise click.UsageError(str(exc))
        numbers, picked = [n for n, _ in kept], [row for _, row in kept]
        if not picked:
            raise click.ClickException("--pick/--exclude left no clips")
        total = sum(clip_seconds(m, d, config.recording) for d, m in picked)
    from highlights.analysis.slowmo import best_kill, choose

    names = clip_names([(label_of[id(demo)], moment) for demo, moment in picked])
    rank_of = {id(moment): i for i, (_, moment) in enumerate(picked, 1)}
    picked_moments = [moment for _, moment in picked]
    slow_picks = choose([best_kill(moment) for _, moment in picked])
    slow_of = {id(moment): pick for (_, moment), pick in zip(picked, slow_picks)}
    slow_cells = ["" if p is None else f"{p.reason} → {p.kill.victim_name}" for p in slow_picks]
    print_top_table(
        picked, slow_cells, clips=names, labels=[label_of[id(demo)] for demo, _ in picked], numbers=numbers
    )
    info(f"{len(picked)} clips, about {total:.0f}s in total, into {output_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for job in jobs:
        mine = [(moment, name) for (demo, moment), name in zip(picked, names) if demo is job.demo]
        if resume:
            done = [name for _, name in mine if (output_dir / f"{name}.mp4").is_file()]
            if done:
                info(f"{job.label}: already done, skipping {', '.join(done)}")
            mine = [(moment, name) for moment, name in mine if name not in done]
        if not mine:
            continue
        info(f"[bold]{job.label}[/bold]: {len(mine)} clip(s)")
        sequences, clips = plan_demo(
            job, mine, config, cinematic, cinematic_path, hold, align, intro_cam, downtime_cam, tilt_mode, grenade_cam,
            intro_transition, protect_kills=not cinematic_only, static_cam=static_cam,
            static_downtime=static_downtime, xray=xray, xray_fade=xray_fade,
        )
        if cinematic_only:
            from highlights.teaser import plan_takes, record_teaser, write_plans

            takes = plan_takes(clips, job.demo, clip_length, job.label)
            if not takes:
                continue
            if json_only:
                write_plans(takes, config, output_dir, speed, output_fps)
            else:
                written += record_teaser(
                    job, takes, config, output_dir, keep_clips or reuse_recordings, reuse_recordings, speed, output_fps
                )
            continue
        match = faceit.lookup(job.demo, config, job.path.name, job.label)
        for clip in clips:
            report = output_dir / f"{clip.name}.json"
            write_report(report, job.demo, clip.group, spatial_by_sequence=clip.spatial)
            data = json.loads(report.read_text(encoding="utf-8"))
            data["batch"] = rank_block(rank_of[id(clip.pov.moments[0])], picked_moments)
            if kill_filter is not None and kill_filter.active:
                data["batch"]["kill_filter"] = {
                    "weapons": sorted(kill_filter.weapons),
                    "headshot_only": kill_filter.headshot_only,
                    "per_kill": kill_filter.per_kill,
                }
            data["slowmo_suggestion"] = slowmo_hint(slow_of.get(id(clip.pov.moments[0])), clip.pov, job.demo.tickrate)
            if match is not None:
                data["match"] = match
            report.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        buildups: dict[int, str] = {}
        if buildup:
            extra, buildups = plan_buildup_sequences(job, clips, config, buildup, buildup_length, len(sequences), output_dir)
            sequences = [*sequences, *extra]
        if json_only:
            continue
        written += _record_demo(
            job, sequences, clips, buildups, config, output_dir, keep_clips or reuse_recordings, timing_offset,
            reuse_recordings, calibrate,
        )
    if written and review:
        from highlights.recording.recorder import resolve_tools
        from highlights.video.review import review_dir

        info("Reviewing cam-path footage and making contact sheets...")
        review_dir(resolve_tools(config, auto_install=False).ffmpeg_exe, output_dir, [p.stem for p in written])
    return written


def plan_buildup_sequences(
    job: DemoJob,
    clips: list[ClipPlan],
    config: Config,
    mode: str,
    max_length: float,
    numbered_so_far: int,
    output_dir: Path,
) -> tuple[list[Sequence], dict[int, str]]:
    """A build-up cam-path shot per clip from the same round before the clip starts; writes each
    `<name>-buildup.json` and returns the sequences (numbered after the others) and id(sequence) -> name."""
    from highlights.broll import plan_buildups, shot_report, shot_sequences
    from highlights.recording.campaths import load_campaths, resolve_campaths_dirs

    paths = load_campaths(resolve_campaths_dirs(config), job.demo.map_name)
    if not paths:
        warn(f"{job.label}: no cam paths for {job.demo.map_name}, no build-up shots")
        return [], {}
    targets = {clip.name: (clip.pov.round_index, clip.pov.start_tick) for clip in clips}
    shots = plan_buildups(job.demo, paths, mode, targets, max_length)
    missing = [name for name in targets if name not in shots]
    if missing:
        warn(f"{job.label}: no {mode} build-up window before {', '.join(missing)}")
    names = list(shots)
    sequences = shot_sequences(job.demo, [shots[n] for n in names], f"build-up {mode}", numbered_so_far + 1)
    by_id: dict[int, str] = {}
    for sequence, name in zip(sequences, names):
        buildup_name = f"{name}-buildup"
        by_id[id(sequence)] = buildup_name
        report = shot_report(job.demo, job.label, mode, shots[name], f"{buildup_name}.mp4")
        report.update({"kind": "buildup", "for_clip": f"{name}.mp4"})
        (output_dir / f"{buildup_name}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        info(f"{buildup_name}: {shots[name].path.name} {shots[name].seconds:.1f}s before the play")
    return sequences, by_id


def _sequence_key(sequence: Sequence, config: Config) -> str:
    """What a recording depends on; a kept recording is reused only when this is unchanged."""
    import hashlib

    campath = ""
    if sequence.campath is not None and Path(sequence.campath).is_file():
        campath = hashlib.sha1(Path(sequence.campath).read_bytes()).hexdigest()
    return json.dumps(
        [
            sequence.start_tick,
            sequence.end_tick,
            [(c.tick, c.steamid) for c in sequence.cameras],
            campath,
            sequence.campath_retime,
            sequence.xray is not None,
            config.recording.fps,
            config.recording.width,
            config.recording.height,
            sorted(config.recording.extra_commands),
            sorted(config.recording.player_names.items()),
        ],
        sort_keys=True,
    )


def record_or_reuse(job: DemoJob, sequences: list[Sequence], config: Config, reuse: bool) -> list:
    """Record the sequences, or with `reuse` only those without an identical kept recording
    (tracked in <work>/recorded.json). Returns the outputs found afterwards."""
    from highlights.recording.recorder import record, verify_outputs

    manifest_path = job.work_dir / "recorded.json"
    manifest: dict[str, str] = {}
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except ValueError:
            manifest = {}
    keys = {s.name: _sequence_key(s, config) for s in sequences}
    todo = sequences
    if reuse:
        todo = [
            s for s in sequences
            if manifest.get(s.name) != keys[s.name] or not (job.work_dir / s.name / "video.mp4").is_file()
        ]
        if len(todo) < len(sequences):
            info(f"{job.label}: reusing {len(sequences) - len(todo)} kept recording(s), recording {len(todo)}")
    if todo:
        record(job.demo, todo, config, job.work_dir)
    outputs = verify_outputs(sequences, job.work_dir)
    for output in outputs:
        manifest[output.sequence.name] = keys[output.sequence.name]
    job.work_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return outputs


def _pov_kill_seconds(pov: Sequence, tickrate: float) -> list[float]:
    ticks = sorted({k.tick for m in pov.moments for k in m.kills if m.steamid == k.attacker_steamid})
    return [(t - pov.start_tick) / tickrate for t in ticks if pov.start_tick <= t <= pov.end_tick]


def _record_demo(
    job: DemoJob,
    sequences: list[Sequence],
    clips: list[ClipPlan],
    buildups: dict[int, str],
    config: Config,
    output_dir: Path,
    keep_clips: bool,
    timing_offset: float,
    reuse: bool = False,
    calibrate: bool = True,
) -> list[Path]:
    from highlights import cli
    from highlights.recording.recorder import RecordingSetupError, resolve_tools
    from highlights.report import finalize_report
    from highlights.video.ffmpeg import FfmpegError, probe_duration
    from highlights.video.postprocess import process_clip

    try:
        outputs = record_or_reuse(job, sequences, config, reuse)
    except RecordingSetupError as exc:
        warn(f"{job.label}: recording failed, skipping this demo: {exc}")
        return []
    if not outputs:
        warn(f"{job.label}: recording produced no clips, skipping this demo")
        return []

    tools = resolve_tools(config, auto_install=False)
    clips_dir = job.work_dir / "clips"
    info(f"{job.label}: recording done, post-processing {len(outputs)} sequence(s)...")
    recorded = []
    for index, o in enumerate(outputs, 1):
        info(f"{job.label}: preparing clip {index}/{len(outputs)}...")
        recorded.append((o, process_clip(tools.ffmpeg_exe, o, clips_dir, False)))

    # Kill-feed timing, measured on the plain POV clips before anything is cut into them.
    offsets: dict[int, tuple[float | None, int, int]] = {}
    if calibrate:
        from highlights.video.killfeed_sync import feed_activity, measure_offset, spikes

        for output, clip in recorded:
            sequence = output.sequence
            if sequence.cinematic_of is not None or id(sequence) in buildups or not sequence.moments:
                continue
            kills = _pov_kill_seconds(sequence, job.demo.tickrate)
            try:
                offset, matched = measure_offset(kills, spikes(feed_activity(tools.ffmpeg_exe, clip)))
            except FfmpegError as exc:
                warn(f"{job.label}: kill-feed timing check failed: {exc}")
                continue
            offsets[id(sequence)] = (offset, matched, len(kills))

    if any(o.sequence.splice_segments for o, _ in recorded):
        recorded = cli._splice_cinematics(
            tools.ffmpeg_exe, recorded, clips_dir, config.recording.crf, (config.recording.width, config.recording.height)
        )

    name_of = {id(c.pov.moments[0]): c.name for c in clips}
    written = []
    for output, clip in recorded:
        sequence = output.sequence
        if sequence.static_cam:
            target = output_dir / f"{name_of[id(sequence.moments[0])]}-static-{sequence.static_cam['slug']}.mp4"
            shutil.copy2(clip, target)
            written.append(target)
            info(f"Wrote {target}")
            continue
        if sequence.cinematic_of is not None:
            continue
        if id(sequence) in buildups:
            target = output_dir / f"{buildups[id(sequence)]}.mp4"
            shutil.copy2(clip, target)
            written.append(target)
            info(f"Wrote {target}")
            continue
        name = name_of[id(sequence.moments[0])]
        target = output_dir / f"{name}.mp4"
        shutil.copy2(clip, target)
        measured, matched, total = offsets.get(id(sequence), (None, 0, 0))
        report_path = output_dir / f"{name}.json"
        finalize_report(
            report_path,
            {1: probe_duration(tools.ffmpeg_exe, target)},
            offset_seconds=timing_offset + (measured or 0.0),
        )
        if calibrate:
            _note_kill_sync(report_path, measured, matched, total)
        written.append(target)
        info(f"Wrote {target}" + (f" (kill times shifted {measured:+.2f}s from the kill feed)" if measured else ""))
    missing = [c.name for c in clips if output_dir.joinpath(f"{c.name}.mp4") not in written]
    if missing:
        warn(f"{job.label}: no recording for {', '.join(missing)}")
    if not keep_clips:
        shutil.rmtree(job.work_dir, ignore_errors=True)
    return written


def _note_kill_sync(report_path: Path, measured: float | None, matched: int, total: int) -> None:
    """Record the kill-feed measurement in the report and shift the slow-mo hint with it."""
    data = json.loads(report_path.read_text(encoding="utf-8"))
    timing = data["editing"]["video_timing"]
    timing["kill_sync"] = {"measured_offset_seconds": measured, "kills_matched": matched, "kills": total}
    if measured is not None:
        timing["status"] = "calibrated_from_kill_feed"
        hint = data.get("slowmo_suggestion")
        if hint:
            for key in ("clip_time_seconds", "start_seconds", "end_seconds"):
                hint[key] = round(max(0.0, hint[key] + measured), 3)
    report_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
