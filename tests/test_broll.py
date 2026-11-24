import json
from types import SimpleNamespace

import pytest
from conftest import make_demo, make_round
from test_splice import write_oriented

from highlights import cli
from highlights.broll import BrollShot, plan_demo_broll, shot_sequences
from highlights.recording.campaths import load_campaths

TICKRATE = 64.0


def _demo():
    demo = make_demo([], [make_round(0, 0, 100000, winner="CT")])
    demo.slots = {"111": 1}
    demo.tickrate = TICKRATE
    return demo


def _paths(tmp_path, spec):
    for name, points in spec.items():
        write_oriented(tmp_path / "Nuke", name, points)
    return load_campaths(tmp_path, "de_nuke")


def _sampler(positions):
    """Every sampled tick has the same alive players at fixed positions."""

    def sample(demo, ticks):
        return {tick: [(sid, xyz, True) for sid, xyz in positions.items()] for tick in ticks}

    return sample


FACING = {"facing": [(0, 0, 0, 0, 0, 0), (6, 0, 0, 0, 0, 0)]}  # looks down +x
AWAY = {"away": [(0, 0, 0, 0, 0, 180), (6, 0, 0, 0, 0, 180)]}


def test_players_mode_needs_a_player_in_view(tmp_path):
    paths = _paths(tmp_path, {**FACING, **AWAY})
    sampler = _sampler({"111": (500.0, 0.0, 0.0)})
    shots = plan_demo_broll(_demo(), paths, "players", 3, 8.0, seed=1, sampler=sampler)
    assert shots and {s.path.name for s in shots} == {"facing"}
    assert all(s.presence == 1.0 and s.visible == ["111"] for s in shots)


def test_empty_mode_needs_nobody_in_view(tmp_path):
    paths = _paths(tmp_path, {**FACING, **AWAY})
    sampler = _sampler({"111": (500.0, 0.0, 0.0)})
    shots = plan_demo_broll(_demo(), paths, "empty", 3, 8.0, seed=1, sampler=sampler)
    assert shots and {s.path.name for s in shots} == {"away"}
    assert all(s.visible == [] for s in shots)


def test_dead_players_do_not_count_and_windows_do_not_overlap(tmp_path):
    paths = _paths(tmp_path, FACING)

    def sampler(demo, ticks):
        return {tick: [("111", (500.0, 0.0, 0.0), False)] for tick in ticks}

    shots = plan_demo_broll(_demo(), paths, "empty", 4, 6.0, seed=3, sampler=sampler)
    assert shots
    ordered = sorted(shots, key=lambda s: s.start_tick)
    assert all(a.end_tick <= b.start_tick for a, b in zip(ordered, ordered[1:]))


def test_same_seed_same_pick_and_too_short_paths_are_skipped(tmp_path):
    paths = _paths(tmp_path, {**FACING, "blip": [(0, 0, 0, 0, 0, 0), (1, 0, 0, 0, 0, 0)]})
    sampler = _sampler({"111": (500.0, 0.0, 0.0)})
    a = plan_demo_broll(_demo(), paths, "players", 2, 8.0, seed=7, sampler=sampler)
    b = plan_demo_broll(_demo(), paths, "players", 2, 8.0, seed=7, sampler=sampler)
    assert [(s.start_tick, s.path.name) for s in a] == [(s.start_tick, s.path.name) for s in b]
    assert all(s.path.name == "facing" for s in a)
    with pytest.raises(ValueError):
        plan_demo_broll(_demo(), paths, "bogus", 1, 8.0)


def test_sequences_carry_the_cam_path_and_a_camera(tmp_path):
    paths = _paths(tmp_path, FACING)
    shot = BrollShot(0, paths[0], 1000, 1384, 6.0, 1.0, ["111"], "111")
    [sequence] = shot_sequences(_demo(), [shot], "players")
    assert sequence.campath == paths[0].file and sequence.cinematic_of is None
    assert sequence.cameras[0].steamid == "111" and sequence.cameras[0].tick == 1000


def test_broll_command_json_only_writes_one_file_per_shot(tmp_path, monkeypatch):
    paths = _paths(tmp_path / "cp", FACING)
    demo = _demo()
    demo.path = tmp_path / "a.dem"
    demo.path.write_bytes(b"x")
    monkeypatch.setattr(cli, "parse_demo", lambda path: demo)
    monkeypatch.setattr("highlights.recording.campaths.load_campaths", lambda base, map_name: paths)
    monkeypatch.setattr(
        "highlights.broll.sample_players",
        lambda d, ticks: {t: [("111", (500.0, 0.0, 0.0), True)] for t in ticks},
    )
    from click.testing import CliRunner

    out = tmp_path / "out"
    result = CliRunner().invoke(
        cli.main,
        ["broll", f"m1={demo.path}", "--mode", "players", "--count", "2", "--seed", "1", "--json-only", "-o", str(out)],
    )
    assert result.exit_code == 0, result.output
    files = sorted(out.glob("m1-broll-players-*.json"))
    assert files
    data = json.loads(files[0].read_text())
    assert data["kind"] == "broll" and data["mode"] == "players" and data["campath"] == "facing"
