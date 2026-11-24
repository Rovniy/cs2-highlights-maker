import math

import pytest

from highlights.recording.campaths import load_campaths
from highlights.recording.dyncams import (
    DOWNTIME_MAX,
    EYE_HEIGHT,
    OPENER_SECONDS,
    POV_GAP,
    STYLES,
    Pose,
    Segment,
    build_keyframes,
    eye_pose_at,
    look_at,
    parse_styles,
    pick_style,
    plan_segments,
    segment_pose,
    style_pose,
    write_campath,
)
from highlights.recording.splice_plan import MIN_SEGMENT

EYE = Pose((100.0, 200.0, 64.0), 30.0, 5.0, 90.0)


def close(a, b, tol=1e-6):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def test_look_at_conventions():
    assert look_at((0, 0, 0), (10, 0, 0)) == pytest.approx((0.0, 0.0))
    assert look_at((0, 0, 0), (0, 10, 0))[0] == pytest.approx(90.0)  # +y is positive yaw
    assert look_at((0, 0, 10), (0, 0, 0))[1] > 80  # looking down is positive pitch


def test_eye_pose_adds_eye_height_and_interpolates_yaw_the_short_way():
    track = [(0.0, (0, 0, 0), 350.0, 0.0), (1.0, (10, 0, 0), 10.0, 10.0)]
    pose = eye_pose_at(track, 0.5)
    assert pose.pos == (5.0, 0.0, EYE_HEIGHT)
    assert pose.yaw == pytest.approx(0.0, abs=1e-6) or pose.yaw == pytest.approx(360.0)
    assert pose.pitch == pytest.approx(5.0)
    assert eye_pose_at(track, 2.0) is None
    assert eye_pose_at(track, 1.1).pos == (10.0, 0.0, EYE_HEIGHT)  # within the slack: last pose held


@pytest.mark.parametrize("style", STYLES)
def test_every_style_has_a_shot_that_faces_the_player(style):
    pose = style_pose(style, EYE, 0.0)
    assert math.dist(pose.pos, EYE.pos) > 15  # a head is ~12 units: the camera never sits in it
    if style not in ("tps", "shoulder"):  # these look where the player looks, the others at them
        yaw, _ = look_at(pose.pos, EYE.pos)
        assert abs((pose.yaw - yaw + 180) % 360 - 180) < 15


def test_face_is_in_front_and_tps_behind():
    f = (math.cos(math.radians(EYE.yaw)), math.sin(math.radians(EYE.yaw)))
    for style, sign in (("face", 1), ("tps", -1), ("shoulder", -1)):
        pose = style_pose(style, EYE, 0.0)
        along = (pose.pos[0] - EYE.pos[0]) * f[0] + (pose.pos[1] - EYE.pos[1]) * f[1]
        assert along * sign > 20


def test_shoulder_is_close_behind_and_to_the_players_right():
    f = (math.cos(math.radians(EYE.yaw)), math.sin(math.radians(EYE.yaw)))
    r = (f[1], -f[0])  # the player's right
    pose = style_pose("shoulder", EYE, 0.0)
    dx, dy = pose.pos[0] - EYE.pos[0], pose.pos[1] - EYE.pos[1]
    assert -45 < dx * f[0] + dy * f[1] < -25  # tight: much closer than tps
    assert 5 < dx * r[0] + dy * r[1] < 25
    assert pose.yaw == pytest.approx(EYE.yaw) and pose.pitch == pytest.approx(EYE.pitch)


def test_unknown_style_is_rejected():
    with pytest.raises(ValueError):
        style_pose("drone", EYE, 0.0)


@pytest.mark.parametrize("style", STYLES)
def test_intro_ends_exactly_on_the_eye(style):
    intro = Segment(0.0, 3.0, "intro", style)
    end = segment_pose(intro, EYE, 3.0)
    assert close(end.pos, EYE.pos) and end.yaw == pytest.approx(EYE.yaw) and end.fov == pytest.approx(EYE.fov)
    assert not close(segment_pose(intro, EYE, 0.0).pos, EYE.pos, 1.0)


@pytest.mark.parametrize("style", ["face", "gun", "profile"])
def test_intro_swoop_keeps_clear_of_the_players_head_until_the_last_push(style):
    intro = Segment(0.0, 3.5, "intro", style)
    distances = [math.dist(segment_pose(intro, EYE, 3.5 * i / 70).pos, EYE.pos) for i in range(0, 61)]
    assert min(distances) > 14  # up to 87% of the way: never through the head (a head is ~6 units across)


@pytest.mark.parametrize("style", STYLES)
def test_cut_intro_holds_the_shot_to_the_end_without_swooping(style):
    cut = Segment(0.0, 3.0, "intro", style, swoop=False)
    poses = [segment_pose(cut, EYE, 3.0 * i / 30) for i in range(31)]
    assert all(math.dist(p.pos, EYE.pos) > 15 for p in poses)  # never pushes into the eye
    assert close(poses[-1].pos, style_pose(style, EYE, 1.0).pos)  # still on the chosen shot at the cut
    assert close(poses[0].pos, segment_pose(Segment(0.0, 3.0, "intro", style), EYE, 0.0).pos)  # same opening


@pytest.mark.parametrize("style", STYLES)
def test_downtime_is_one_continuous_pass_that_never_swoops_to_the_eye(style):
    down = Segment(2.0, 8.0, "downtime", style)
    poses = [segment_pose(down, EYE, 2.0 + 6.0 * i / 20) for i in range(21)]
    assert all(math.dist(p.pos, EYE.pos) > 15 for p in poses)
    steps = [math.dist(a.pos, b.pos) for a, b in zip(poses, poses[1:])]
    assert max(steps) < 60  # smooth drift, no jumps between samples


def test_plan_opener_stops_before_the_first_kill():
    segs = plan_segments(20.0, [4.0, 10.0], [], "face", None)
    assert segs == [Segment(0.0, 3.0, "intro", "face")]  # kill at 4.0 minus the 1 s padding
    assert plan_segments(20.0, [2.0], [], "face", None) == []  # not even a minimum cut fits
    assert plan_segments(20.0, [9.0], [], "face", None)[0].end == OPENER_SECONDS


def test_plan_downtime_uses_gaps_caps_length_and_leaves_a_pov_gap():
    segs = plan_segments(30.0, [4.0, 20.0], [], "face", "tps")
    assert [s.kind for s in segs] == ["intro", "downtime", "downtime"]  # opener, gap between kills, tail
    down = segs[1]
    assert down.start >= 3.0 + POV_GAP and down.start == pytest.approx(5.0)  # after the kill's padding
    assert down.end - down.start == pytest.approx(DOWNTIME_MAX)  # a full pass, capped
    assert all(s.end - s.start >= MIN_SEGMENT for s in segs)
    # never inside the POV padding around the kills
    assert all(not (s.start < 5.0 and s.end > 3.0) for s in segs if s.kind == "downtime")


def test_plan_avoids_real_path_ranges_and_camera_switches():
    segs = plan_segments(30.0, [2.0, 29.0], [10.0], None, "face", taken=[(5.0, 8.0)])
    spans = [(s.start, s.end) for s in segs]
    assert all(not (a < 8.0 and b > 5.0) for a, b in spans)
    assert all(not (a < 10.0 < b) for a, b in spans)


def test_no_styles_no_segments():
    assert plan_segments(30.0, [10.0], [], None, None) == []


def test_parse_styles_and_pick_style():
    assert parse_styles("Face, tps,face") == ["face", "tps"]
    assert parse_styles("random") == list(STYLES)
    with pytest.raises(ValueError):
        parse_styles("drone")
    assert pick_style(["heli"], 5) == "heli"
    assert pick_style(list(STYLES), 7) == pick_style(list(STYLES), 7)


def test_keyframes_hold_the_first_pose_from_time_zero_and_round_trip(tmp_path):
    segs = [Segment(2.0, 4.0, "downtime", "profile")]
    keys = build_keyframes(segs, lambda t: EYE)
    assert keys[0][0] == 0.0 and keys[1][0] == 2.0 and keys[0][1] == keys[1][1]
    file = write_campath(tmp_path / "Nuke" / "gen.campath", keys)
    path = load_campaths(tmp_path, "de_nuke")[0]
    assert path.duration == pytest.approx(4.0)
    # the quaternion round-trips to the pose's view direction
    pose = keys[len(keys) // 2][1]
    yaw, pitch = math.radians(pose.yaw), math.radians(pose.pitch)
    expected = (math.cos(pitch) * math.cos(yaw), math.cos(pitch) * math.sin(yaw), -math.sin(pitch))
    mid = min(path.keyframes, key=lambda k: abs(k.t - keys[len(keys) // 2][0]))
    assert close(mid.forward, expected, 1e-4)
    assert file.exists()


def test_keyframes_raise_when_the_player_position_is_unknown():
    with pytest.raises(ValueError):
        build_keyframes([Segment(0.0, 2.0, "intro", "face")], lambda t: None)


def test_quaternions_stay_on_one_side_across_a_yaw_wrap(tmp_path):
    keys = [(float(i), Pose((0, 0, 0), yaw, 0.0, 90.0)) for i, yaw in enumerate((178.0, 179.0, 181.0, 182.0))]
    text = write_campath(tmp_path / "w.campath", keys).read_text()
    import re

    quats = [tuple(float(v) for v in m) for m in re.findall(r'qw="([-\d.]+)" qx="([-\d.]+)" qy="([-\d.]+)" qz="([-\d.]+)"', text)]
    assert all(sum(a * b for a, b in zip(q, r)) > 0 for q, r in zip(quats, quats[1:]))


def test_write_campath_refuses_paths_hlae_cannot_play(tmp_path):
    pose = Pose((0, 0, 0), 0.0, 0.0, 90.0)
    with pytest.raises(ValueError, match="at least 4"):
        write_campath(tmp_path / "short.campath", [(0.0, pose), (1.0, pose)])


def test_only_the_longest_passes_are_kept_and_short_gaps_are_skipped():
    kills = [3.0, 6.5, 14.0, 18.5, 29.0]  # gaps: 0-2, 7.5-13, 15-17.5, 19.5-28
    segs = plan_segments(32.0, kills, [], None, "heli")
    assert len(segs) == 2  # MAX_DOWNTIME_PASSES
    lengths = sorted(round(s.end - s.start, 1) for s in segs)
    assert lengths == [5.5, 8.5]
    short = plan_segments(10.0, [3.0, 8.0], [], None, "heli")  # gaps 0-2 (too short), 4-7, 9-10 (too short)
    assert [(s.start, s.end) for s in short] == [(4.0, 7.0)]


def test_face_is_a_tight_close_up_of_the_head():
    from highlights.recording.dyncams import shot

    for u in (0.0, 0.5, 1.0):
        face = shot("face", u)
        assert 16 <= face.radius <= 30 and face.fov <= 75  # a head is ~12 units: this fills most of the frame
        assert 15 <= face.angle <= 50  # front-left three-quarter: off the rifle's line (their right), still facing them


def test_face_swoop_goes_round_the_players_left_and_never_crosses_the_front():
    intro = Segment(0.0, 3.5, "intro", "face")
    f = (math.cos(math.radians(EYE.yaw)), math.sin(math.radians(EYE.yaw)))
    left = (-f[1], f[0])  # the player's left (positive angle)
    poses = [segment_pose(intro, EYE, 3.5 * (0.55 + 0.3 * i / 20)) for i in range(21)]  # the swoop part
    sides = [(p.pos[0] - EYE.pos[0]) * left[0] + (p.pos[1] - EYE.pos[1]) * left[1] for p in poses]
    assert all(side > -1 for side in sides)  # stays on the left until it is behind the head
