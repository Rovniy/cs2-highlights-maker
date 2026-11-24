"""--static-downtime: cut your fixed cameras (the --static-cam cams) into each clip's down time.

The passes are the same down-time stretches `--downtime-cam` uses (`dyncams.plan_segments`: the gaps
between the POV windows around the kills, split at camera switches, minus what a real cam path already
covers, at least DOWNTIME_MIN, at most DOWNTIME_MAX, the MAX_DOWNTIME_PASSES longest). Per pass, every
static cam is judged over it, sampled every STEP seconds:

    pov      the POV player rotating: the cam with the player in view longest (at least MIN_PLAYER_SHARE)
    enemies  the other team: the cam with the most living enemies in view on average (at least one enemy
             in view for MIN_ENEMY_SHARE of the pass)
    auto     enemies when the best enemy cam sees AUTO_ENEMIES or more on average, else pov, else enemies

Each cam that wins a pass becomes one take (the static hold path over the whole clip, as --static-cam
records it) whose `splice_segments` are the passes it won, so the splice step cuts it into the POV clip
like a generated cam. Same view test as the other cams (view cone + floor check, no walls).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from rich.markup import escape

from highlights.demo.parser import DemoData
from highlights.floors import Floors
from highlights.models import Sequence
from highlights.recording.cinematic import _kill_times, _target_at
from highlights.recording.dyn_sequences import _switch_times, insert_takes
from highlights.recording.dyncams import MIN_POINTS, Pose, plan_segments, write_campath
from highlights.recording.splice_plan import Point, _in_view
from highlights.recording.static_cams import StaticCam
from highlights.util.log import info, warn

MODES = ("auto", "pov", "enemies")
STEP = 0.5  # seconds between samples of a pass
MIN_PLAYER_SHARE = 0.5  # pov: the player is in view for at least this share of the pass
MIN_ENEMY_SHARE = 0.5  # enemies: at least one living enemy in view for this share of the pass
AUTO_ENEMIES = 2.0  # auto: the enemy shot wins when it shows this many enemies on average

Snapshots = dict[int, list[tuple[str, Point, bool]]]  # tick -> (steamid, position, alive), as broll samples
PositionSampler = Callable[[DemoData, list[int]], Snapshots]


def plan_passes(pov: Sequence, takes: list[Sequence], tickrate: float) -> list[tuple[float, float]]:
    """Down-time passes of a POV clip (clip seconds), avoiding what its other takes already cut in."""
    taken = [segment for take in takes if take.splice_segments for segment in take.splice_segments]
    segments = plan_segments(
        pov.duration_seconds(tickrate), _kill_times(pov, tickrate), _switch_times(pov, tickrate), None, "static", taken
    )
    return [(s.start, s.end) for s in segments]


def _times(start: float, end: float) -> list[float]:
    return [start + i * STEP for i in range(int((end - start) / STEP + 1e-9) + 1)]


def _tick(pov: Sequence, t: float, tickrate: float) -> int:
    return pov.start_tick + round(t * tickrate)


def _enemies(demo: DemoData, pov: Sequence, tick: int) -> set[str]:
    """Steamids on the other side from whoever the POV camera is on at this tick."""
    roster = demo.rosters.get(pov.round_index, {})
    active = next((c.steamid for c in sorted(pov.cameras, key=lambda c: c.tick, reverse=True) if c.tick <= tick), None)
    side = roster.get(active or "")
    return {sid for sid, s in roster.items() if side and s != side}


def assess_pass(
    cam: StaticCam,
    pov: Sequence,
    start: float,
    end: float,
    spatial: dict,
    demo: DemoData,
    snapshots: Snapshots,
    floors: Floors | None = None,
) -> dict:
    """What a cam shows of one pass: share of it with the player in view, enemies in view."""
    tickrate = float(demo.tickrate)
    forward = cam.forward
    target_at = _target_at(pov, spatial, tickrate)
    times = _times(start, end)
    player = enemy_samples = enemy_count = 0
    seen: set[str] = set()
    for t in times:
        if (p := target_at(t)) is not None and _in_view(cam.pos, forward, cam.fov, p, floors=floors):
            player += 1
        tick = _tick(pov, t, tickrate)
        enemies = _enemies(demo, pov, tick)
        visible = [
            sid for sid, xyz, alive in snapshots.get(tick, [])
            if alive and sid in enemies and _in_view(cam.pos, forward, cam.fov, xyz, floors=floors)
        ]  # fmt: skip
        enemy_samples += bool(visible)
        enemy_count += len(visible)
        seen.update(visible)
    return {
        "cam": cam.name,
        "player_share": round(player / len(times), 2),
        "enemy_share": round(enemy_samples / len(times), 2),
        "enemies_avg": round(enemy_count / len(times), 2),
        "enemies_seen": sorted(demo.player_names.get(s, s) for s in seen),
    }


def choose(looks: list[dict], mode: str) -> tuple[dict, str] | None:
    """The winning cam of a pass and what it shows ("pov" or "enemies"), None when none qualifies."""
    pov = sorted((a for a in looks if a["player_share"] >= MIN_PLAYER_SHARE), key=lambda a: -a["player_share"])
    enemies = sorted(
        (a for a in looks if a["enemy_share"] >= MIN_ENEMY_SHARE), key=lambda a: (-a["enemies_avg"], -a["enemy_share"])
    )
    if mode == "pov":
        return (pov[0], "pov") if pov else None
    if mode == "enemies":
        return (enemies[0], "enemies") if enemies else None
    if enemies and enemies[0]["enemies_avg"] >= AUTO_ENEMIES:
        return enemies[0], "enemies"
    if pov:
        return pov[0], "pov"
    return (enemies[0], "enemies") if enemies else None


def add_static_downtime(
    sequences: list[Sequence],
    cams: list[StaticCam],
    spatial_by_sequence: dict[int, dict],
    demo: DemoData,
    mode: str,
    out_dir: Path,
    sample: PositionSampler,
    floors: Floors | None = None,
) -> tuple[list[Sequence], dict[int, dict]]:
    """Add, per POV clip, one take per static cam that wins a down-time pass (spliced in by the splice step)."""
    tickrate = float(demo.tickrate)
    povs = [s for s in sequences if s.cinematic_of is None]
    passes = {pov.number: plan_passes(pov, [s for s in sequences if s.cinematic_of == pov.number], tickrate) for pov in povs}
    snapshots: Snapshots = {}
    if mode != "pov":
        ticks = sorted({
            _tick(pov, t, tickrate) for pov in povs for a, b in passes[pov.number] for t in _times(a, b)
        })  # fmt: skip
        snapshots = sample(demo, ticks) if ticks else {}
    by_name = {c.name: c for c in cams}
    new_for: dict[int, list[Sequence]] = {}
    for pov in povs:
        spatial = spatial_by_sequence.get(pov.number, {})
        won: dict[str, list[dict]] = {}
        for start, end in passes[pov.number]:
            looks = [assess_pass(c, pov, start, end, spatial, demo, snapshots, floors) for c in cams]
            picked = choose(looks, mode)
            if picked is None:
                info(escape(f"{pov.label}: no static cam shows the down time at {start:.1f}-{end:.1f}s"))
                continue
            look, shows = picked
            won.setdefault(look["cam"], []).append({"start_seconds": start, "end_seconds": end, "shows": shows, **look})
            what = (
                f"enemies {', '.join(look['enemies_seen'])} (avg {look['enemies_avg']:.1f})"
                if shows == "enemies"
                else f"the player {look['player_share']:.0%} of it"
            )
            info(escape(f"{pov.label}: static cam {look['cam']} for the down time at {start:.1f}-{end:.1f}s — {what}"))
        takes = []
        duration = pov.duration_seconds(tickrate)
        for name, cuts in won.items():
            cam = by_name[name]
            pose = Pose(cam.pos, cam.yaw, cam.pitch, cam.fov)
            hold = [(duration * i / (MIN_POINTS - 1), pose) for i in range(MIN_POINTS)]
            takes.append(
                replace(
                    pov,
                    label=f"{pov.label} — static {cam.name} (down time)",
                    campath=write_campath(out_dir / f"staticdt_{cam.slug}_{pov.start_tick}.campath", hold),
                    cinematic_of=pov.number,
                    splice_segments=[(c["start_seconds"], c["end_seconds"]) for c in cuts],
                    campath_retime=None,
                    slowmo=None,
                    tilt=None,
                    cinematic_candidates=None,
                    dynamic_cam=None,
                    static_cam=None,
                    static_downtime=[{**c, "slug": cam.slug} for c in cuts],
                )
            )
        if takes:
            new_for[pov.number] = takes
    if povs and not new_for:
        warn(f"--static-downtime {mode}: no static cam shows the down time of any clip")
    return insert_takes(sequences, spatial_by_sequence, new_for)
