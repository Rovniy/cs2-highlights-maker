import pytest
from conftest import make_demo, make_round
from test_dyn_sequences import pov
from test_static_cams import SPATIAL, StaticCam

from highlights.floors import LEVEL_GAP, Floors, floors_for
from highlights.places import PlaceMap
from highlights.recording.splice_plan import _in_view
from highlights.recording.static_cams import assess

# Two stacked levels over the same ground, Nuke-style: upper floor at z -416, lower at -750.
UPPER = [(x, y, -416.0) for x in range(-600, 601, 100) for y in range(-600, 601, 100)]
LOWER = [(x, y, -750.0) for x in range(-600, 601, 100) for y in range(-600, 601, 100)]
FLOORS = Floors.from_points(UPPER + LOWER)
CAMERA = (0.0, -500.0, -250.0)  # above the upper floor, looking down toward +y
DOWN = (0.0, 0.70710678, -0.70710678)


def test_floor_under_the_camera_is_the_highest_level_below_it():
    assert FLOORS.floor_under(CAMERA) == -416.0  # the upper floor (32-unit height bins)
    assert FLOORS.floor_under((0.0, 0.0, -600.0)) == -768.0  # under the upper floor: the lower one
    assert FLOORS.floor_under((5000.0, 5000.0, 0.0)) is None  # nobody ever stood near


def test_targets_on_a_lower_level_are_hidden_same_level_ones_are_not():
    assert FLOORS.hides(CAMERA, (0.0, 0.0, -750.0))
    assert not FLOORS.hides(CAMERA, (0.0, 0.0, -416.0))
    assert not FLOORS.hides(CAMERA, (0.0, 0.0, -416.0 - LEVEL_GAP + 1))  # a ramp down, still this level
    assert not FLOORS.hides((5000.0, 5000.0, 0.0), (5000.0, 5100.0, -2000.0))  # no floor data: no check


def test_view_cone_drops_the_level_below_only_with_floors():
    below = (0.0, 0.0, -750.0)
    assert _in_view(CAMERA, DOWN, 90.0, below)  # the cone alone sees through the floor
    assert not _in_view(CAMERA, DOWN, 90.0, below, floors=FLOORS)
    assert _in_view(CAMERA, DOWN, 90.0, (0.0, -100.0, -416.0), floors=FLOORS)


def test_static_cam_does_not_see_a_death_on_the_level_below():
    # SPATIAL: player at the origin (z 0), victim dies at (0, 1000, 0); this cam stands on a level 400 up.
    cam = StaticCam("above", (0.0, -800.0, 700.0), pitch=30.0, yaw=90.0)
    stacked = Floors.from_points([(x, y, 400.0) for x in range(-800, 801, 100) for y in range(-900, 1101, 100)])
    assert assess(cam, pov(1), SPATIAL, 64.0, {})["deaths_seen"] == ["222"]
    blocked = assess(cam, pov(1), SPATIAL, 64.0, {}, stacked)
    assert blocked["deaths_seen"] == [] and blocked["player_seconds"] == 0


def test_floors_for_samples_live_play_and_adds_the_place_map():
    demo = make_demo([], [make_round(0, 0, 1640)])  # freeze time ends at 1000
    asked = []

    def sampler(d, ticks):
        asked.extend(ticks)
        return [(0.0, 0.0, -416.0)] * len(ticks)

    place_map = PlaceMap("de_nuke")
    for _ in range(3):
        place_map.add(0.0, 0.0, -750.0, "BombsiteB")
    floors = floors_for(demo, place_map, sampler)
    assert asked == list(range(1000, 1641, 64))  # live play only, once a second
    assert floors.floor_under((0.0, 0.0, -250.0)) == -416.0
    assert floors.floor_under((0.0, 0.0, -600.0)) == -720.0  # the place cell's middle (-768..-672)
    assert floors_for(demo, None, lambda d, t: []) is None


@pytest.mark.parametrize("z", [-416.0, -300.0])
def test_floor_lookup_is_cached_per_position(z):
    floors = Floors.from_points(UPPER)
    assert floors.floor_under((0.0, 0.0, z)) == floors.floor_under((1.0, 1.0, z))


def test_one_off_heights_are_not_floors_and_far_cameras_search_wider():
    floors = Floors.from_points(UPPER + [(0.0, -500.0, -300.0)])  # one sample on a box top
    assert floors.floor_under(CAMERA) == -416.0
    behind_fence = (0.0, -900.0, -250.0)  # 300 units from the nearest player position
    assert floors.floor_under(behind_fence) == -416.0
