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


RoundRule = tuple[str | None, int]  # (demo label or None for every demo, 1-based round number)


def parse_rounds(values: Collection[str]) -> list[RoundRule]:
    """`--round` values: "13", "13,15" or "semi-finals:13,15" (that demo only; the demo applies to the
    bare numbers after it in the same value); repeats mix. Raises ValueError on anything else."""
    rules: list[RoundRule] = []
    for value in values:
        scope: str | None = None
        for part in (p.strip() for p in value.split(",")):
            if not part:
                continue
            label, sep, number = part.rpartition(":")
            if not number.strip().isdigit() or int(number) < 1 or (sep and not label.strip()):
                raise ValueError(f"Bad --round value {part!r} — use a round number like 13 or DEMO:13")
            if sep:
                scope = label.strip().lower()
            rules.append((scope, int(number)))
    return rules


def round_allowed(rules: list[RoundRule], label: str, round_index: int) -> bool:
    """True when there are no rules or one matches this demo label and 0-based round index."""
    return not rules or any(
        number == round_index + 1 and (scope is None or scope == label.lower()) for scope, number in rules
    )


def parse_numbers(values: Collection[str]) -> list[int]:
    """`--pick`/`--exclude` values: "1,2,4", "3-6" and repeats mix (1-based). Raises ValueError."""
    numbers: list[int] = []
    for value in values:
        for part in (p.strip() for p in value.split(",")):
            if not part:
                continue
            first, sep, last = part.partition("-")
            if not first.strip().isdigit() or (sep and not last.strip().isdigit()):
                raise ValueError(f"Bad number {part!r} — use row numbers like 1,2,4 or 3-6")
            low, high = int(first), int(last) if sep else int(first)
            if low < 1 or high < low:
                raise ValueError(f"Bad range {part!r}")
            numbers.extend(n for n in range(low, high + 1) if n not in numbers)
    return numbers


def apply_picks(rows: list, pick: list[int], exclude: list[int]) -> list[tuple[int, object]]:
    """(table number, row) for the rows kept by --pick/--exclude; numbers are positions in `rows`
    (1-based, the "#" of the table the same command prints without them). Raises ValueError for a
    number past the end."""
    for number in [*pick, *exclude]:
        if number > len(rows):
            raise ValueError(f"Row {number} does not exist — the table has {len(rows)} rows")
    numbered = list(enumerate(rows, 1))
    if pick:
        numbered = [numbered[n - 1] for n in pick]
    return [(n, row) for n, row in numbered if n not in exclude]


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


def clip_seconds(moment: Moment, demo: DemoData, recording: RecordingConfig) -> float:
    """Length of the clip recorded for `moment` (before any overlap merge)."""
    start, end = _clip_bounds(moment, demo, recording)
    return (end - start) / demo.tickrate


def pick_rows(
    rows: list[tuple[DemoData, Moment]],
    selection: SelectionConfig,
    recording: RecordingConfig,
) -> tuple[list[tuple[DemoData, Moment]], float]:
    """Best-first pick from moments pooled over several demos, honoring `top` and `max_duration`.

    A clip that would overrun the duration budget is skipped so shorter ones can still fit.
    Returns the picked rows (best first) and their summed clip length."""
    ranked = sorted(rows, key=lambda row: row[1].score, reverse=True)
    picked: list[tuple[DemoData, Moment]] = []
    total = 0.0
    for demo, moment in ranked:
        if selection.top > 0 and len(picked) >= selection.top:
            break
        seconds = clip_seconds(moment, demo, recording)
        if selection.max_duration > 0 and total + seconds > selection.max_duration:
            continue
        picked.append((demo, moment))
        total += seconds
    return picked, total


def select_sequences(
    moments: list[Moment],
    demo: DemoData,
    selection: SelectionConfig,
    recording: RecordingConfig,
    player_steamids: Collection[str] | None = None,
    merge: bool = True,
) -> list[Sequence]:
    candidates = eligible_moments(moments, selection, player_steamids)
    picked = [moment for _, moment in pick_rows([(demo, m) for m in candidates], selection, recording)[0]]
    return build_sequences(picked, demo, selection, recording, merge=merge)


def build_sequences(
    picked: list[Moment],
    demo: DemoData,
    selection: SelectionConfig,
    recording: RecordingConfig,
    merge: bool = True,
) -> list[Sequence]:
    """Sequences for the picked moments, in tick order. Overlapping or nearly touching moments
    become one sequence with camera switches unless `merge` is off (one sequence per moment)."""
    picked.sort(key=lambda m: m.first_tick)

    # Merge moments whose clips overlap or nearly touch into a single sequence
    # with camera switches between the featured players.
    merge_gap_ticks = int(selection.merge_gap_seconds * demo.tickrate)
    groups: list[list[Moment]] = []
    group_end = -1
    for moment in picked:
        start, end = _clip_bounds(moment, demo, recording)
        if merge and groups and start <= group_end + merge_gap_ticks:
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
