import json

import click.testing
import pytest
from conftest import make_demo, make_kill, make_round

import highlights.cli as cli
from highlights.config import Config

A = "76561198000000001"
B = "76561198000000002"
C = "76561198000000003"


def _demo():
    # Three players, each with a lone single kill in its own round. With the
    # default min_kills=2 none of these would qualify without a player filter.
    rounds = [
        make_round(0, 0, 100000, winner="CT"),
        make_round(1, 100000, 200000, winner="T"),
        make_round(2, 200000, 300000, winner="CT"),
    ]
    kills = [
        make_kill(1000, round_index=0, attacker=A, victim=B),
        make_kill(110000, round_index=1, attacker=B, victim=C),
        make_kill(210000, round_index=2, attacker=C, victim=A),
    ]
    return make_demo(kills, rounds)


@pytest.fixture
def runner_and_demo(tmp_path, monkeypatch):
    demo = _demo()
    monkeypatch.setattr(cli, "parse_demo", lambda path: demo)
    monkeypatch.setattr(cli, "ensure_dem_file", lambda path, work_dir: path)
    monkeypatch.setattr(cli, "load_config", lambda: Config())
    monkeypatch.setattr(cli, "_resolve_work_dir", lambda config, demo_path: tmp_path)
    dem_file = tmp_path / "m.dem"
    dem_file.write_bytes(b"")
    return click.testing.CliRunner(), dem_file


def _run(runner, dem_file, *extra):
    result = runner.invoke(
        cli.main,
        [str(dem_file), "--json-only", "-o", str(dem_file.parent / "out.mp4"), *extra],
    )
    assert result.exit_code == 0, result.output
    report = json.loads((dem_file.parent / "out.json").read_text(encoding="utf-8"))
    return result, report


def _steamids(report):
    ids = set()
    for seq in report["sequences"]:
        ids.update(c["steamid"] for c in seq["cameras"])
        ids.update(m["steamid"] for m in seq["moments"])
    return ids


def test_players_option_comma_separated(runner_and_demo):
    runner, dem_file = runner_and_demo
    _, report = _run(runner, dem_file, "--players", f"{A},{B}")
    assert _steamids(report) == {A, B}


def test_players_option_repeated_and_mixed(runner_and_demo):
    runner, dem_file = runner_and_demo
    _, report = _run(runner, dem_file, "--players", A, "--players", f"player{B}")
    assert _steamids(report) == {A, B}


def test_player_and_players_combine(runner_and_demo):
    runner, dem_file = runner_and_demo
    _, report = _run(runner, dem_file, "--player", A, "--players", B)
    assert _steamids(report) == {A, B}


def test_players_unknown_fails(runner_and_demo):
    runner, dem_file = runner_and_demo
    result = runner.invoke(
        cli.main,
        [str(dem_file), "--json-only", "-o", str(dem_file.parent / "out.mp4"),
         "--players", "76561198000000099"],
    )
    assert result.exit_code != 0
    assert "not found" in result.output


def test_single_player_option_still_works(runner_and_demo):
    runner, dem_file = runner_and_demo
    _, report = _run(runner, dem_file, "--player", A)
    assert _steamids(report) == {A}