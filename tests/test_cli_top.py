from pathlib import Path

import click.testing
import pytest
from conftest import make_demo, make_kill, make_round

import highlights.cli as cli
from highlights.config import Config


def _demo_for(name: str, kills, rounds, map_name):
    demo = make_demo(kills, rounds)
    demo.path = Path(name)
    demo.map_name = map_name
    return demo


def _demos():
    # make_kill prefixes names with "player". Expected scores:
    # alice 3.4 (round 0, two headshots) > carl 3.2 (round 0) > bob 2.8
    # (final round, lost, 0.85 * 1.3 = 1.105).
    alice, bob, carl = "alice", "bob", "carl"
    v1, v2 = "v1", "v2"
    rounds_a = [make_round(0, 0, 100000, winner="CT"), make_round(1, 100000, 200000, winner="T")]
    kills_a = [
        make_kill(2000, round_index=0, attacker=alice, victim=v1, headshot=True),
        make_kill(3000, round_index=0, attacker=alice, victim=v2, headshot=True),
        make_kill(110000, round_index=1, attacker=bob, victim=v1),
        make_kill(111000, round_index=1, attacker=bob, victim=v2),
    ]
    rounds_b = [make_round(0, 0, 100000, winner="T")]
    kills_b = [
        make_kill(2000, round_index=0, attacker=carl, victim=v1),
        make_kill(3000, round_index=0, attacker=carl, victim=v2),
    ]
    return (
        {
            "a.dem": _demo_for("a.dem", kills_a, rounds_a, "de_mirage"),
            "b.dem": _demo_for("b.dem", kills_b, rounds_b, "de_inferno"),
        },
        (alice, bob, carl),
    )


@pytest.fixture
def runner_and_demos(tmp_path, monkeypatch):
    demos, names = _demos()
    monkeypatch.setattr(cli, "parse_demo", lambda path: demos[Path(path).name])
    monkeypatch.setattr(cli, "ensure_dem_file", lambda path, work_dir: path)
    monkeypatch.setattr(cli, "load_config", lambda: Config())
    monkeypatch.setattr(cli, "_resolve_work_dir", lambda config, demo_path: tmp_path)
    files = []
    for name in demos:
        dem_file = tmp_path / name
        dem_file.write_bytes(b"")
        files.append(dem_file)
    return click.testing.CliRunner(), files, names


def _top(runner, files, *extra):
    result = runner.invoke(cli.main, ["top", *[str(f) for f in files], *extra], env={"COLUMNS": "200"})
    return result


def _table_rows(output):
    """Table rows as (rank, demo, map, round, player, highlight, score, length) tuples."""
    rows = []
    for line in output.splitlines():
        parts = [p for p in (c.strip() for c in line.split("│")) if p]
        if len(parts) == 8 and parts[0].isdigit():
            rows.append(tuple(parts))
    return rows


def _players(output):
    return [row[4] for row in _table_rows(output)]


def _scores(output):
    return [float(row[6]) for row in _table_rows(output)]


def test_top_pools_moments_across_demos_by_score(runner_and_demos):
    runner, files, (alice, bob, carl) = runner_and_demos
    result = _top(runner, files)
    assert result.exit_code == 0, result.output
    assert _players(result.output) == [f"player{alice}", f"player{carl}", f"player{bob}"]
    assert _scores(result.output) == sorted(_scores(result.output), reverse=True)
    assert "de_mirage" in result.output and "de_inferno" in result.output


def test_top_option_keeps_single_best(runner_and_demos):
    runner, files, (alice, bob, carl) = runner_and_demos
    result = _top(runner, files, "--top", "1")
    assert result.exit_code == 0, result.output
    rows = _table_rows(result.output)
    assert len(rows) == 1
    assert rows[0][0] == "1" and rows[0][4] == f"player{alice}"


def test_top_players_skips_demo_without_player(runner_and_demos):
    runner, files, (alice, bob, carl) = runner_and_demos
    result = _top(runner, files, "--players", "alice")
    assert result.exit_code == 0, result.output
    assert _players(result.output) == [f"player{alice}"]
    assert "b.dem" in result.output  # warning naming the skipped demo
    assert "Warning" in result.output


def test_top_unknown_player_fails(runner_and_demos):
    runner, files, _ = runner_and_demos
    result = _top(runner, files, "--players", "nobody")
    assert result.exit_code != 0
    assert "nobody" in result.output


def test_top_writes_no_files(runner_and_demos, tmp_path):
    runner, files, _ = runner_and_demos
    result = _top(runner, files)
    assert result.exit_code == 0, result.output
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.dem", "b.dem"]


def test_default_group_still_dispatches_to_run(tmp_path, monkeypatch):
    # A double kill in a won round qualifies with the default min_kills=2.
    alice, v1, v2 = "alice", "v1", "v2"
    rounds = [make_round(0, 0, 100000, winner="CT")]
    kills = [
        make_kill(2000, round_index=0, attacker=alice, victim=v1),
        make_kill(3000, round_index=0, attacker=alice, victim=v2),
    ]
    demo = _demo_for("m.dem", kills, rounds, "de_test")
    monkeypatch.setattr(cli, "parse_demo", lambda path: demo)
    monkeypatch.setattr(cli, "ensure_dem_file", lambda path, work_dir: path)
    monkeypatch.setattr(cli, "load_config", lambda: Config())
    monkeypatch.setattr(cli, "_resolve_work_dir", lambda config, demo_path: tmp_path)
    dem_file = tmp_path / "m.dem"
    dem_file.write_bytes(b"")
    result = click.testing.CliRunner().invoke(
        cli.main,
        [str(dem_file), "--json-only", "-o", str(tmp_path / "out.mp4")],
    )
    assert result.exit_code == 0, result.output


def test_top_max_duration_limits_total_length(runner_and_demos):
    runner, files, _ = runner_and_demos
    everything = _table_rows(_top(runner, files).output)
    assert len(everything) >= 2
    tiny = _top(runner, files, "--max-duration", "1")
    assert tiny.exit_code != 0 and "No clip fits" in tiny.output
    full = _top(runner, files, "--max-duration", "100000")
    assert full.exit_code == 0, full.output
    assert len(_table_rows(full.output)) == len(everything)
    assert "of 1666m 40s" in full.output


def test_top_takes_folders_recursively(tmp_path, runner_and_demos):
    runner, files, _ = runner_and_demos
    sub = tmp_path / "season" / "week1"
    sub.mkdir(parents=True)
    files[1].rename(sub / files[1].name)
    flat = runner.invoke(cli.main, ["top", str(tmp_path)])
    assert "de_mirage" in flat.output and "de_inferno" not in flat.output
    deep = runner.invoke(cli.main, ["top", str(tmp_path), "--recursive"])
    assert deep.exit_code == 0, deep.output
    assert "de_mirage" in deep.output and "de_inferno" in deep.output


def test_top_match_column_from_faceit(runner_and_demos, monkeypatch):
    from highlights import faceit

    runner, files, (alice, bob, carl) = runner_and_demos
    block = {"teams": ["Alpha", "Bravo"], "division": "ESEA Main",
             "players": {alice: {"team": "Alpha", "opponent": "Bravo"}}}
    monkeypatch.setattr(faceit, "enabled", lambda config, paths: True)
    monkeypatch.setattr(faceit, "lookup", lambda demo, config: block if demo.path.name == "a.dem" else None)
    result = runner.invoke(cli.main, ["top", *map(str, files)], env={"COLUMNS": "250"})
    assert result.exit_code == 0, result.output
    assert "Match" in result.output and "Alpha vs Bravo · ESEA Main" in result.output
    assert "Alpha vs Bravo · ESEA Main" in next(line for line in result.output.splitlines() if "alice" in line)


def test_top_has_no_match_column_without_a_key(runner_and_demos, monkeypatch):
    monkeypatch.delenv("FACEIT_API_KEY", raising=False)
    runner, files, _ = runner_and_demos
    result = runner.invoke(cli.main, ["top", *map(str, files)])
    assert result.exit_code == 0 and "Match" not in result.output


def test_top_shows_each_clip_length_and_the_total(runner_and_demos):
    from highlights.report import minutes_seconds

    runner, files, _ = runner_and_demos
    result = _top(runner, files)
    assert result.exit_code == 0, result.output
    lengths = [float(row[7].rstrip("s")) for row in _table_rows(result.output)]
    # lead-in 4 s + lead-out 3 s around the kills: every clip is at least 7 s
    assert lengths and all(length >= 7 for length in lengths)
    # the total is exact; the column is rounded per clip, so they differ by at most half a second a clip
    total = float(result.output.split("Total: ")[1].split("(")[1].split("s,")[0])
    assert abs(total - sum(lengths)) <= len(lengths) / 2
    assert f"Total: {minutes_seconds(total)}" in result.output


def test_minutes_seconds():
    from highlights.report import minutes_seconds

    assert minutes_seconds(245.4) == "4m 05s"
    assert minutes_seconds(59.6) == "1m 00s"
    assert minutes_seconds(0) == "0m 00s"


def test_top_weapon_filter_per_kill(runner_and_demos):
    runner, files, (alice, bob, carl) = runner_and_demos
    result = _top(runner, files, "--headshot-only", "--per-kill")
    assert result.exit_code == 0, result.output
    rows = _table_rows(result.output)
    assert [r[4] for r in rows] == [f"player{alice}", f"player{alice}"]
    assert all(r[7] == "4s" for r in rows)  # 2 s lead-in + 1.5 s lead-out, rounded


def test_top_weapon_filter_without_match_lists_weapons(runner_and_demos):
    runner, files, _ = runner_and_demos
    result = _top(runner, files, "--weapon", "deagle")
    assert result.exit_code != 0
    assert "weapons in these demos: ak47" in result.output


def test_run_per_kill_keeps_each_kill_its_own_sequence(tmp_path, monkeypatch):
    # Two deagle kills one second apart would merge into one clip; --per-kill keeps them apart.
    rounds = [make_round(0, 0, 100000, winner="CT")]
    kills = [
        make_kill(2000, attacker="alice", victim="v1", weapon="deagle"),
        make_kill(2064, attacker="alice", victim="v2", weapon="deagle"),
        make_kill(9000, attacker="alice", victim="v3", weapon="ak47"),
    ]
    demo = _demo_for("m.dem", kills, rounds, "de_test")
    monkeypatch.setattr(cli, "parse_demo", lambda path: demo)
    monkeypatch.setattr(cli, "ensure_dem_file", lambda path, work_dir: path)
    monkeypatch.setattr(cli, "load_config", lambda: Config())
    monkeypatch.setattr(cli, "_resolve_work_dir", lambda config, demo_path: tmp_path)
    dem_file = tmp_path / "m.dem"
    dem_file.write_bytes(b"")
    out = tmp_path / "out.mp4"
    result = click.testing.CliRunner().invoke(
        cli.main, [str(dem_file), "--json-only", "-o", str(out), "--weapon", "deag", "--per-kill"]
    )
    assert result.exit_code == 0, result.output
    import json

    sequences = json.loads(out.with_suffix(".json").read_text())["sequences"]
    assert [(s["end_tick"] - s["start_tick"]) / 64 for s in sequences] == [3.5, 3.5]
