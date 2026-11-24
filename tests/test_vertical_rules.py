"""Floor-awareness rules: the camera cannot see through the floor, and path ranking
weights height difference so same-floor paths win."""

from pathlib import Path

import pytest
from test_campath_parser import point, write_campath
from test_splice import static_path

from highlights.recording.campaths import (
    VERTICAL_WEIGHT,
    CamPath,
    Keyframe,
    parse_campath,
    path_distance,
    rank_campaths,
)
from highlights.recording.splice_plan import CAM_BELOW_TOLERANCE, _in_view, plan_splice


def test_in_view_allows_player_slightly_below_camera():
    """A target 500 ahead and 10 below the camera is inside the cone."""
    assert _in_view((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), 90.0, (500.0, 0.0, -10.0))


def test_in_view_rejects_camera_far_below_player():
    """A camera more than the tolerance under the player cannot see them."""
    target = (500.0, 0.0, CAM_BELOW_TOLERANCE + 1.0)
    assert not _in_view((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), 90.0, target)


def test_in_view_boundary_at_tolerance_is_visible():
    """A player exactly at the tolerance above the camera still counts as visible."""
    target = (500.0, 0.0, CAM_BELOW_TOLERANCE)
    assert _in_view((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), 90.0, target)


def test_in_view_camera_above_player_is_allowed():
    """A camera 200 above a target 500 ahead is inside the vertical cone (fov 90)."""
    assert _in_view((0.0, 0.0, 200.0), (1.0, 0.0, 0.0), 90.0, (500.0, 0.0, 0.0))


def test_plan_drops_underground_path_but_keeps_surface_one(tmp_path):
    """An underground camera looking up at a surface player plans nothing; the same
    path at floor level plans a cut, so the height rule is what removed it."""
    underground = static_path(tmp_path / "a", pos=(0, 0, -300))
    target = lambda t: (500.0, 0.0, 0.0)  # noqa: E731
    assert plan_splice(20.0, underground, [], target) == []
    surface = static_path(tmp_path / "b", pos=(0, 0, 0))
    assert plan_splice(20.0, surface, [], target) != []


def test_path_distance_horizontal_only_is_plain_distance(tmp_path):
    """A point 100 away on the same floor measures 100."""
    file = write_campath(tmp_path, "flat", [point(0, x=100), point(5, x=100)])
    path = parse_campath(file)
    assert path_distance(path, [(0.0, 0.0, 0.0)]) == pytest.approx(100.0)


def test_path_distance_weights_vertical_gap(tmp_path):
    """A point 100 straight below the target measures VERTICAL_WEIGHT * 100."""
    file = write_campath(tmp_path, "under", [point(0, z=-100), point(5, z=-100)])
    path = parse_campath(file)
    assert path_distance(path, [(0.0, 0.0, 0.0)]) == pytest.approx(VERTICAL_WEIGHT * 100.0)


def _cam(name, pos):
    keyframes = (
        Keyframe(0.0, pos, (1, 0, 0), 90.0),
        Keyframe(1.0, pos, (1, 0, 0), 90.0),
    )
    return CamPath(name=name, file=Path(f"{name}.dz"), keyframes=keyframes, duration=1.0)


def test_rank_campaths_prefers_same_floor_path():
    """A same-floor path 150 away outranks one 100 away horizontally but 100 below."""
    same_floor = _cam("same", (150.0, 0.0, 0.0))
    underground = _cam("under", (100.0, 0.0, -100.0))
    ranked = rank_campaths([underground, same_floor], [(0.0, 0.0, 0.0)])
    assert [p.name for p in ranked] == ["same", "under"]
