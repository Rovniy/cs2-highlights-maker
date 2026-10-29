"""Select the best Moments and turn them into recordable Sequences."""

from __future__ import annotations

from collections.abc import Collection

from highlights.config import RecordingConfig, SelectionConfig
from highlights.demo.parser import DemoData
from highlights.models import CameraAction, Moment, Sequence

MIN_SAFE_TICK = 96  # the game may skip the first ~1.5s of ticks after demo_gototick 0


def eligible_moments(
    moments: list[Moment],
    selection: SelectionConfig,
    player_steamids: Collection[str] | None = None,
) -> list[Moment]:
    min_kills = 1 if player_steamids else selection.min_kills
    result = []
    for moment in moments:
        if player_steamids and moment.steamid not in player_steamids:
            continue
        has_clutch = moment.clutch is not None and (moment.clutch.won or moment.clutch.kills >= 2)
        has_special = any(k.is_special for k in moment.kills)
        if moment.kill_count >= min_kills or has_clutch or has_special:
            result.append(moment)
    return result


def _clip_bounds(moment: Moment, demo: DemoData, recording: RecordingConfig) -> tuple[int, int]:
    tickrate = demo.tickrate
    round_ = demo.round_at(moment.round_index)
    start = moment.first_tick - int(recording.lead_in_seconds * tickrate)
    end = moment.last_tick + int(recording.lead_out_seconds * tickrate)
    if round_ is not None:
        start = max(start, round_.freeze_end_tick)
        end = min(end, round_.end_tick + int(5 * tickrate))
    start = max(start, MIN_SAFE_TICK)
    end = min(end, demo.last_tick)
    if moment.clutch is not None and moment.clutch.won:
        end = max(end, min(moment.clutch.end_tick + int(tickrate), demo.last_tick))
    return start, end


def select_sequences(
    moments: list[Moment],
    demo: DemoData,
    selection: SelectionConfig,
    recording: RecordingConfig,
    player_steamids: Collection[str] | None = None,
) -> list[Sequence]:
    candidates = eligible_moments(moments, selection, player_steamids)
    candidates.sort(key=lambda m: m.score, reverse=True)

    picked: list[Moment] = []
    total_seconds = 0.0
    for moment in candidates:
        if selection.top > 0 and len(picked) >= selection.top:
            break
        start, end = _clip_bounds(moment, demo, recording)
        duration = (end - start) / demo.tickrate
        if selection.max_duration > 0 and total_seconds + duration > selection.max_duration:
            continue
        picked.append(moment)
        total_seconds += duration

    picked.sort(key=lambda m: m.first_tick)

    # Merge moments whose clips overlap or nearly touch into a single sequence
    # with camera switches between the featured players.
    merge_gap_ticks = int(selection.merge_gap_seconds * demo.tickrate)
    groups: list[list[Moment]] = []
    group_end = -1
    for moment in picked:
        start, end = _clip_bounds(moment, demo, recording)
        if groups and start <= group_end + merge_gap_ticks:
            groups[-1].append(moment)
            group_end = max(group_end, end)
        else:
            groups.append([moment])
            group_end = end

    sequences: list[Sequence] = []
    for group in groups:
        bounds = [_clip_bounds(m, demo, recording) for m in group]
        start_tick = min(b[0] for b in bounds)
        end_tick = max(b[1] for b in bounds)

        cameras: list[CameraAction] = []
        for moment in group:
            # Switch the camera shortly before the player's action starts.
            switch_tick = max(start_tick, moment.first_tick - int(demo.tickrate))
            if not cameras or cameras[-1].steamid != moment.steamid:
                cameras.append(CameraAction(tick=switch_tick, steamid=moment.steamid))
        # The first camera must be set at the very start of the clip.
        cameras[0] = CameraAction(tick=start_tick, steamid=cameras[0].steamid)

        label = " + ".join(f"{m.name} — {m.describe()}" for m in group)
        label += f" — R{group[0].round_index + 1}"
        sequences.append(
            Sequence(
                number=len(sequences) + 1,
                start_tick=start_tick,
                end_tick=end_tick,
                cameras=cameras,
                label=label,
                score=round(sum(m.score for m in group), 3),
                round_index=group[0].round_index,
                moments=group,
            )
        )
    return sequences
