"""Parser-level tests for campaths.py using tiny generated XML only (never real packs)."""

import math
from pathlib import Path

import pytest

from highlights.config import Config
from highlights.recording.campaths import (
    CamPath,
    Keyframe,
    default_campaths_dir,
    load_campaths,
    parse_campath,
    path_distance,
    pose_at,
    rank_campaths,
    resolve_campaths_dirs,
)
from highlights.recording.path_routes import load_labels
from highlights.recording.static_cams import load_static_cams


def write_campath(folder, name="p", points=(), suffix=".dz"):
    """Write a tiny campath XML; points is a list of attribute dicts so each test
    controls exactly which attributes are present. Returns the file."""
    folder.mkdir(parents=True, exist_ok=True)
    body = "".join(
        "<p " + " ".join(f'{key}="{value}"' for key, value in attrs.items()) + "/>" for attrs in points
    )
    file = folder / f"{name}{suffix}"
    file.write_text(f"<campath><points>{body}</points></campath>")
    return file


def point(t, x=0.0, y=0.0, z=0.0, **extra):
    """A point with the attributes HLAE needs (t/x/y/z plus rx/ry/rz) and any extras
    (fov, qw/qx/qy/qz...); tests that omit an attribute truly omit it."""
    attrs = {"t": t, "x": x, "y": y, "z": z, "rx": 0, "ry": 0, "rz": 0}
    attrs.update(extra)
    return attrs


def _close(v, expected):
    return all(abs(a - b) < 1e-6 for a, b in zip(v, expected))


# --- parse_campath -----------------------------------------------------------


def test_parse_missing_file_returns_none(tmp_path):
    """A nonexistent file parses to None."""
    assert parse_campath(tmp_path / "nope.dz") is None


def test_parse_malformed_xml_returns_none(tmp_path):
    """Unparseable XML returns None instead of raising."""
    file = tmp_path / "bad.dz"
    file.write_text("<campath><points><p t=")
    assert parse_campath(file) is None


def test_parse_single_point_returns_none(tmp_path):
    """Fewer than two usable points is not a path."""
    file = write_campath(tmp_path, "one", [point(0)])
    assert parse_campath(file) is None


def test_parse_zero_points_returns_none(tmp_path):
    """A campath with no points returns None."""
    file = write_campath(tmp_path, "empty", [])
    assert parse_campath(file) is None


def test_parse_skips_points_with_missing_or_bad_attributes(tmp_path):
    """A <p> missing a required attribute or with a non-numeric value is skipped;
    the remaining points still parse."""
    good_a, good_b = point(0, x=1), point(5, x=2)
    no_x = {"t": 1, "y": 0, "z": 0, "rx": 0, "ry": 0, "rz": 0}  # x is missing
    bad_t = point("abc", x=3)
    file = write_campath(tmp_path, "mixed", [good_a, no_x, bad_t, good_b])
    path = parse_campath(file)
    assert path is not None
    assert [k.t for k in path.keyframes] == [0.0, 5.0]
    assert [k.pos for k in path.keyframes] == [(1.0, 0.0, 0.0), (2.0, 0.0, 0.0)]


def test_parse_sorts_by_time_and_rebases_to_zero(tmp_path):
    """Out-of-order points are sorted and times rebased so the first keyframe is t=0."""
    file = write_campath(
        tmp_path, "shuffled", [point(30, x=3), point(10, x=1), point(20, x=2), point(5, x=0)]
    )
    path = parse_campath(file)
    assert [k.t for k in path.keyframes] == [0.0, 5.0, 15.0, 25.0]
    assert path.duration == 25.0
    assert [k.pos[0] for k in path.keyframes] == [0.0, 1.0, 2.0, 3.0]


def test_parse_defaults_fov_to_90(tmp_path):
    """A point without a fov attribute gets 90 degrees."""
    file = write_campath(tmp_path, "nofov", [point(0), point(1)])
    path = parse_campath(file)
    assert all(k.fov == 90.0 for k in path.keyframes)


def test_parse_name_and_file(tmp_path):
    """name is the file stem and file is the resolved path."""
    file = write_campath(tmp_path, "A4", [point(0), point(1)])
    path = parse_campath(file)
    assert path.name == "A4"
    assert path.file == file.resolve()


def test_parse_euler_conventions(tmp_path):
    """Euler angles: yaw 0 looks along +x, yaw 90 toward +y, pitch 90 looks down."""
    file = write_campath(
        tmp_path,
        "euler",
        [
            point(0, rx=0, ry=0, rz=0),
            point(1, rx=0, ry=0, rz=90),
            point(2, rx=0, ry=90, rz=0),
        ],
    )
    path = parse_campath(file)
    assert _close(path.keyframes[0].forward, (1, 0, 0))
    assert _close(path.keyframes[1].forward, (0, 1, 0))
    assert _close(path.keyframes[2].forward, (0, 0, -1))


def test_parse_identity_quaternion_forward_is_x(tmp_path):
    """The identity quaternion looks along +x."""
    file = write_campath(
        tmp_path, "quat", [point(0, qw=1, qx=0, qy=0, qz=0), point(1, qw=1, qx=0, qy=0, qz=0)]
    )
    path = parse_campath(file)
    assert _close(path.keyframes[0].forward, (1, 0, 0))


def test_parse_quaternion_wins_over_euler(tmp_path):
    """When both are present and conflicting, the quaternion decides the direction."""
    half = math.sqrt(0.5)  # 90-degree yaw about z: +x -> +y
    file = write_campath(
        tmp_path,
        "both",
        [
            point(0, rx=0, ry=0, rz=0, qw=half, qx=0, qy=0, qz=half),
            point(1, rx=0, ry=0, rz=0, qw=half, qx=0, qy=0, qz=half),
        ],
    )
    path = parse_campath(file)
    assert _close(path.keyframes[0].forward, (0, 1, 0))


def test_parse_non_unit_quaternion_still_gives_unit_forward(tmp_path):
    """A quaternion of length 2 yields a unit-length forward vector."""
    file = write_campath(
        tmp_path,
        "big",
        [point(0, qw=2, qx=0, qy=0, qz=0), point(1, qw=2, qx=0, qy=0, qz=0)],
    )
    path = parse_campath(file)
    forward = path.keyframes[0].forward
    assert math.isclose(math.dist(forward, (0, 0, 0)), 1.0, abs_tol=1e-9)
    assert _close(forward, (1, 0, 0))


def test_parse_partial_quaternion_falls_back_to_euler(tmp_path):
    """With only some of qw/qx/qy/qz present, the Euler angles are used instead."""
    file = write_campath(
        tmp_path,
        "partial",
        [
            point(0, rx=0, ry=0, rz=90, qw=1, qx=0, qy=0),  # quaternion would say +x
            point(1, rx=0, ry=0, rz=90, qw=1, qx=0, qy=0),
        ],
    )
    path = parse_campath(file)
    assert _close(path.keyframes[0].forward, (0, 1, 0))  # euler rz=90 wins


# --- pose_at ------------------------------------------------------------------


def test_pose_outside_range_returns_none(tmp_path):
    """Times before 0 or after the duration return None."""
    file = write_campath(tmp_path, "r", [point(0, x=0), point(10, x=100)])
    path = parse_campath(file)
    assert pose_at(path, -0.001) is None
    assert pose_at(path, 10.001) is None


def test_pose_at_endpoints_returns_keyframe_poses(tmp_path):
    """t=0 and t=duration return the first and last keyframe poses."""
    file = write_campath(
        tmp_path,
        "ends",
        [point(0, x=0, fov=60, ry=0, rz=0), point(10, x=100, fov=100, ry=0, rz=0)],
    )
    path = parse_campath(file)
    pos, forward, fov = pose_at(path, 0)
    assert pos == (0.0, 0.0, 0.0) and fov == 60.0 and _close(forward, (1, 0, 0))
    pos, forward, fov = pose_at(path, 10)
    assert pos == (100.0, 0.0, 0.0) and fov == 100.0 and _close(forward, (1, 0, 0))


def test_pose_interpolates_position_and_fov_linearly(tmp_path):
    """Midway between two keyframes, position and fov are the midpoint."""
    file = write_campath(
        tmp_path,
        "mid",
        [point(0, x=0, y=0, z=0, fov=60), point(10, x=100, y=-20, z=4, fov=100)],
    )
    path = parse_campath(file)
    pos, _, fov = pose_at(path, 5)
    assert pos == pytest.approx((50.0, -10.0, 2.0))
    assert fov == pytest.approx(80.0)


def test_pose_interpolated_forward_is_unit_length(tmp_path):
    """Blending two different view directions still gives a unit forward vector."""
    file = write_campath(tmp_path, "dirs", [point(0, ry=0, rz=0), point(10, ry=0, rz=90)])
    path = parse_campath(file)
    _, forward, _ = pose_at(path, 5)
    assert math.isclose(math.dist(forward, (0, 0, 0)), 1.0, abs_tol=1e-9)
    assert _close(forward, (math.sqrt(0.5), math.sqrt(0.5), 0.0))


def test_pose_duplicate_timestamps_do_not_raise(tmp_path):
    """Two keyframes with the same t do not cause a ZeroDivisionError."""
    file = write_campath(tmp_path, "dup", [point(0, x=0), point(0, x=50), point(10, x=100)])
    path = parse_campath(file)
    assert pose_at(path, 0) is not None
    assert pose_at(path, 5)[0] == pytest.approx((75.0, 0.0, 0.0))  # midway on the (50 -> 100) leg


# --- load_campaths ------------------------------------------------------------


def test_load_missing_base_dir_returns_empty(tmp_path):
    """A base directory that does not exist yields no paths."""
    assert load_campaths(tmp_path / "missing", "de_nuke") == []


def test_load_matches_map_folder_case_insensitively(tmp_path):
    """Map de_nuke loads from folders named Nuke, de_nuke or NUKE (case-insensitive)."""
    # macOS APFS is case-insensitive, so the three spellings live in separate bases.
    for base_name, folder_name in (("a", "Nuke"), ("b", "de_nuke"), ("c", "NUKE")):
        write_campath(tmp_path / base_name / folder_name, f"cam_{folder_name}", [point(0), point(1)])
        assert [p.name for p in load_campaths(tmp_path / base_name, "de_nuke")] == [f"cam_{folder_name}"]


def test_load_ignores_other_map_folders(tmp_path):
    """Folders named for other maps are not read."""
    write_campath(tmp_path / "Nuke", "wanted", [point(0), point(1)])
    write_campath(tmp_path / "Mirage", "unwanted", [point(0), point(1)])
    paths = load_campaths(tmp_path, "de_nuke")
    assert [p.name for p in paths] == ["wanted"]


def test_load_filters_by_suffix(tmp_path):
    """Only .dz/.campath/.xml (any case) load; .txt and suffix-less files are ignored."""
    folder = tmp_path / "Nuke"
    for name, suffix in (("a", ".dz"), ("b", ".CAMPATH"), ("c", ".XML"), ("d", ".txt"), ("e", "")):
        write_campath(folder, name, [point(0), point(1)], suffix=suffix)
    paths = load_campaths(tmp_path, "de_nuke")
    assert sorted(p.name for p in paths) == ["a", "b", "c"]


def test_load_ignores_loose_files_and_sorts_by_name(tmp_path):
    """Files directly in base are ignored; the result is sorted by name."""
    write_campath(tmp_path, "loose", [point(0), point(1)])
    for name in ("b", "a", "c"):
        write_campath(tmp_path / "Nuke", name, [point(0), point(1)])
    paths = load_campaths(tmp_path, "de_nuke")
    assert [p.name for p in paths] == ["a", "b", "c"]


# --- path_distance / rank_campaths / dirs -------------------------------------


def test_path_distance_is_mean_nearest_distance(tmp_path):
    """Mean over targets of the nearest path point, hand-computed."""
    file = write_campath(tmp_path, "two", [point(0, x=0), point(5, x=10)])
    path = parse_campath(file)
    # target (3,0,0): nearest point (0,0,0) -> 3; target (11,0,0): nearest (10,0,0) -> 1.
    assert path_distance(path, [(3.0, 0.0, 0.0), (11.0, 0.0, 0.0)]) == pytest.approx(2.0)


def test_rank_campaths_empty_inputs_return_empty():
    """No paths or no targets both give an empty ranking."""
    keyframes = (Keyframe(0.0, (0, 0, 0), (1, 0, 0), 90.0), Keyframe(1.0, (1, 0, 0), (1, 0, 0), 90.0))
    path = CamPath(name="a", file=Path("a.dz"), keyframes=keyframes, duration=1.0)
    assert rank_campaths([], [(0.0, 0.0, 0.0)]) == []
    assert rank_campaths([path], []) == []


def test_rank_campaths_breaks_ties_by_name(tmp_path):
    """Equally distant paths are ordered by name."""
    folder = tmp_path / "Nuke"
    for name in ("zeta", "alpha"):
        write_campath(folder, name, [point(0, x=100), point(5, x=100)])
    paths = load_campaths(tmp_path, "de_nuke")
    ranked = rank_campaths(paths, [(0.0, 0.0, 0.0)])
    assert [p.name for p in ranked] == ["alpha", "zeta"]


def test_resolve_campaths_dirs_auto_override_and_list(tmp_path):
    """Default config resolves to the repo campaths folder; an explicit path wins; a list keeps its
    order, "auto" inside it is the repo folder and duplicates are dropped."""
    assert resolve_campaths_dirs(Config()) == [default_campaths_dir()]
    assert default_campaths_dir().name == "campaths"
    config = Config()
    config.paths.campaths_dir = str(tmp_path)
    assert resolve_campaths_dirs(config) == [tmp_path]
    config.paths.campaths_dir = [str(tmp_path / "free"), "auto", str(tmp_path / "free")]
    assert resolve_campaths_dirs(config) == [tmp_path / "free", default_campaths_dir()]
    config.paths.campaths_dir = []
    assert resolve_campaths_dirs(config) == [default_campaths_dir()]


def test_several_dirs_are_merged_and_the_first_wins_a_name(tmp_path):
    """Paths, static cams and labels come from every folder; on a name clash the earlier folder wins."""
    free, paid = tmp_path / "free", tmp_path / "paid"
    write_campath(free / "Nuke", "A1", [point(0, x=1), point(1)])
    write_campath(free / "Nuke", "B1", [point(0), point(1)])
    write_campath(paid / "de_nuke", "a1", [point(0, x=2), point(1)])
    write_campath(paid / "de_nuke", "Heaven", [point(0), point(1)])
    (free / "Nuke" / "labels.toml").write_text('A1 = "free A"\n')
    (paid / "de_nuke" / "labels.toml").write_text('a1 = "paid A"\nHeaven = "heaven"\n')
    (free / "Nuke" / "static.toml").write_text('[ramp]\npos = [0, 0, 0]\nangles = [0, 0]\n')
    (paid / "de_nuke" / "static.toml").write_text('[Ramp]\npos = [9, 9, 9]\nangles = [0, 0]\n[silo]\npos = [1, 1, 1]\nangles = [0, 0]\n')
    paths = load_campaths([free, paid], "de_nuke")
    assert [p.name for p in paths] == ["A1", "B1", "Heaven"] and paths[0].keyframes[0].pos[0] == 1.0
    assert load_labels([free, paid], "de_nuke") == {"a1": "free A", "heaven": "heaven"}
    cams = load_static_cams([free, paid], "de_nuke")
    assert [(c.name, c.pos) for c in cams] == [("ramp", (0.0, 0.0, 0.0)), ("silo", (1.0, 1.0, 1.0))]
    assert [p.name for p in load_campaths([tmp_path / "missing", paid], "de_nuke")] == ["Heaven", "a1"]
