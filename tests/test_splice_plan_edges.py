import math

import pytest

from test_splice import static_path

from highlights.recording.splice_plan import (
    KILL_PAD,
    KILL_PAD_FLOOR,
    MAX_DISTANCE,
    MERGE_GAP,
    MIN_SEGMENT,
    SAMPLE_STEP,
    VIEW_MARGIN,
    _in_view,
    _protected_windows,
    coverage,
    plan_splice,
    position_at,
)

# ---- position_at -----------------------------------------------------------


def test_position_at_empty_trajectory_is_none():
    """An empty trajectory has no position at any time."""
    assert position_at([], 0.0) is None
    assert position_at([], 5.0) is None


def test_position_at_outside_sample_range_is_none():
    """Times before the first or after the last sample return None."""
    samples = [(1.0, (1.0, 2.0, 3.0)), (2.0, (4.0, 5.0, 6.0))]
    assert position_at(samples, 0.99) is None
    assert position_at(samples, 2.01) is None


def test_position_at_exact_endpoints_returns_sample_positions():
    """A time exactly on the first or last sample returns that sample's position."""
    samples = [(1.0, (1.0, 2.0, 3.0)), (2.0, (4.0, 5.0, 6.0))]
    assert position_at(samples, 1.0) == (1.0, 2.0, 3.0)
    assert position_at(samples, 2.0) == (4.0, 5.0, 6.0)


def test_position_at_interpolates_linearly_between_samples():
    """Midway between two samples the position is the midpoint of their points."""
    samples = [(0.0, (0.0, 10.0, -10.0)), (2.0, (100.0, 20.0, -30.0))]
    assert position_at(samples, 1.0) == (50.0, 15.0, -20.0)


def test_position_at_single_sample_only_at_its_own_time():
    """A one-sample trajectory answers only exactly at its timestamp."""
    samples = [(3.0, (7.0, 8.0, 9.0))]
    assert position_at(samples, 3.0) == (7.0, 8.0, 9.0)
    assert position_at(samples, 2.9) is None
    assert position_at(samples, 3.1) is None


# ---- _in_view --------------------------------------------------------------


def test_in_view_target_directly_ahead_is_visible():
    """A target straight ahead beyond the 50-unit floor and within range is in view."""
    assert _in_view((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), 90.0, (500.0, 0.0, 0.0))


def test_in_view_target_behind_camera_is_not_visible():
    """A target behind the camera fails the ahead test."""
    assert not _in_view((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), 90.0, (-500.0, 0.0, 0.0))


def test_in_view_distance_bounds():
    """Targets at or inside 50 units, or past MAX_DISTANCE, are never in view."""
    assert not _in_view((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), 90.0, (50.0, 0.0, 0.0))
    assert not _in_view((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), 90.0, (MAX_DISTANCE + 1.0, 0.0, 0.0))
    assert _in_view((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), 90.0, (MAX_DISTANCE, 0.0, 0.0))


def test_in_view_looking_straight_up_is_never_visible():
    """A camera looking straight up has no usable horizontal basis, so nothing is in view."""
    assert not _in_view((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), 90.0, (500.0, 0.0, -100.0))


def test_in_view_horizontal_cone_edge_uses_view_margin():
    """A target just past half-FOV * VIEW_MARGIN horizontally is out; just inside is in."""
    fov = 90.0
    limit = math.radians(fov) / 2 * VIEW_MARGIN
    ahead = 500.0
    lateral = ahead * math.tan(limit)
    forward = (1.0, 0.0, 0.0)
    inside = (ahead, lateral * 0.99, 0.0)
    outside = (ahead, lateral * 1.01, 0.0)
    assert _in_view((0.0, 0.0, 0.0), forward, fov, inside)
    assert not _in_view((0.0, 0.0, 0.0), forward, fov, outside)


# ---- _protected_windows ----------------------------------------------------


def test_protected_windows_no_kills_is_empty():
    """No kills means nothing is protected."""
    assert _protected_windows([]) == []


def test_protected_windows_single_kill_gets_full_padding():
    """A lone kill is padded by KILL_PAD on both sides."""
    assert _protected_windows([4.0]) == [(4.0 - KILL_PAD, 4.0 + KILL_PAD)]


def test_protected_windows_ignores_input_order():
    """Unsorted input yields the same windows as sorted input."""
    assert _protected_windows([15.0, 4.0, 9.0]) == _protected_windows([4.0, 9.0, 15.0])


def test_protected_windows_at_min_gap_keep_full_padding():
    """Kills exactly MIN_SEGMENT + 2*KILL_PAD apart keep full padding and leave a MIN_SEGMENT gap."""
    gap = MIN_SEGMENT + 2 * KILL_PAD
    windows = _protected_windows([0.0, gap])
    assert windows == [(-KILL_PAD, KILL_PAD), (gap - KILL_PAD, gap + KILL_PAD)]
    assert windows[1][0] - windows[0][1] == MIN_SEGMENT


def test_protected_windows_closer_than_floor_gap_leave_no_usable_gap():
    """Kills closer than MIN_SEGMENT + 2*KILL_PAD_FLOOR keep floor padding between them, leaving no usable gap."""
    gap = MIN_SEGMENT + 2 * KILL_PAD_FLOOR - 0.1
    first, second = _protected_windows([0.0, gap])
    # Between the kills the padding shrinks to the floor; outside the pair it stays full.
    assert first == (-KILL_PAD, KILL_PAD_FLOOR)
    assert second == (gap - KILL_PAD_FLOOR, gap + KILL_PAD)
    # The gap left between the windows is below MIN_SEGMENT, so no cut fits: the
    # pair is protected as one block (plan_splice can never cut between them).
    assert second[0] - first[1] < MIN_SEGMENT


# ---- plan_splice -----------------------------------------------------------


def test_plan_splice_unknown_target_yields_no_segments(tmp_path):
    """When the player's position is never known, nothing can be cut in."""
    path = static_path(tmp_path)
    assert plan_splice(10.0, path, [], lambda t: None) == []


def test_plan_splice_zero_duration_yields_no_segments(tmp_path):
    """A zero-length clip plans no segments and does not crash."""
    path = static_path(tmp_path)
    assert plan_splice(0.0, path, [], lambda t: (500.0, 0.0, 0.0)) == []


def test_plan_splice_drops_runs_shorter_than_min_segment(tmp_path):
    """A visible run below MIN_SEGMENT is dropped; one at/above it is kept."""
    path = static_path(tmp_path)
    edge = MIN_SEGMENT - 2 * SAMPLE_STEP  # two samples short of MIN_SEGMENT

    def brief(t):
        """Visible only during a stretch two steps short of MIN_SEGMENT."""
        return (500.0, 0.0, 0.0) if 3.0 <= t <= 3.0 + edge else None

    assert plan_splice(10.0, path, [], brief) == []

    def kept(t):
        """Visible only during a stretch of exactly MIN_SEGMENT."""
        return (500.0, 0.0, 0.0) if 3.0 <= t <= 3.0 + MIN_SEGMENT else None

    assert plan_splice(10.0, path, [], kept) == [(3.0, round(3.0 + MIN_SEGMENT + SAMPLE_STEP, 2))]


def test_plan_splice_absorbs_short_blips_but_splits_long_gaps(tmp_path):
    """A POV blip shorter than MERGE_GAP is absorbed; a clearly longer gap splits the segment."""
    path = static_path(tmp_path)

    def blip(t):
        """Visible 0..10 except a hole shorter than MERGE_GAP + SAMPLE_STEP around 5 s."""
        if 5.0 <= t < 5.0 + MERGE_GAP - 2 * SAMPLE_STEP:
            return None
        return (500.0, 0.0, 0.0) if 0.0 <= t <= 10.0 else None

    assert plan_splice(10.0, path, [], blip) == [(0.0, 10.0)]

    def split(t):
        """Visible 0..10 except a hole clearly wider than MERGE_GAP around 5 s."""
        if 5.0 <= t < 5.0 + MERGE_GAP + 3 * SAMPLE_STEP:
            return None
        return (500.0, 0.0, 0.0) if 0.0 <= t <= 10.0 else None

    assert plan_splice(10.0, path, [], split) == [(0.0, 5.0), (5.0 + MERGE_GAP + 3 * SAMPLE_STEP, 10.0)]


def test_plan_splice_clamps_segment_end_to_duration(tmp_path):
    """A visible run reaching the end of the clip is clamped to the clip duration."""
    path = static_path(tmp_path, duration=10.0)
    segments = plan_splice(6.35, path, [], lambda t: (500.0, 0.0, 0.0))
    assert segments[-1][1] == 6.35


# ---- coverage --------------------------------------------------------------


def test_coverage_of_no_segments_is_zero():
    """No segments cover no time."""
    assert coverage([]) == 0.0


def test_coverage_sums_segment_lengths():
    """Coverage is the total length of all segments."""
    assert coverage([(0.0, 2.5), (5.5, 13.5)]) == pytest.approx(2.5 + 8.0)
