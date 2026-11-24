import json
from pathlib import Path

import click.testing
import pytest
from conftest import make_demo, make_kill, make_round

import highlights.cli as cli
from highlights.batch import clip_names, parse_demo_arg, present_players, rank_block, slugify, team_steamids
from highlights.config import Config
from highlights.models import Moment


def test_parse_demo_arg_label_and_plain_path():
    assert parse_demo_arg("season59-match07=C:\\demos\\a.dem") == ("season59-match07", Path("C:\\demos\\a.dem"))
    assert parse_demo_arg("/x/y/match.dem") == ("match", Path("/x/y/match.dem"))


def test_slugify_matches_existing_file_names():
    assert slugify("deviN-") == "devin"
    assert slugify("h0t_s0up") == "h0t_s0up"
    assert slugify("-kiwi") == "kiwi"
    assert slugify("!!!", "765") == "765"


def test_clip_names_count_per_label_and_player_in_rank_order():
    def moment(name):
        return Moment(steamid="1", name=name, side="CT", round_index=0)

    rows = [("m7", moment("Ann")), ("m7", moment("Bob")), ("m8", moment("Ann")), ("m7", moment("Ann"))]
    assert clip_names(rows) == ["m7-ann-1", "m7-bob-1", "m8-ann-1", "m7-ann-2"]


def test_rank_block_is_relative_to_the_best_clip():
    picked = [Moment(steamid="1", name="a", side="CT", round_index=0, score=s) for s in (80.0, 40.0)]
    assert rank_block(2, picked) == {"rank": 2, "of": 2, "score": 40.0, "relative_score": 0.5}


def test_team_steamids_reads_cotw_names_file(tmp_path):
    names = tmp_path / "names.json"
    names.write_text(json.dumps({
        "111": {"name": "A", "team": "Saltcrew"},
        "222": {"team": "Ubiquiti"},
        "333": {"name": "C"},
        "bob": {"team": "Saltcrew"},
        "444": "Dee",
    }))
    assert team_steamids(names, ["saltcrew"]) == {"111"}
    assert team_steamids(names, ["Saltcrew", "ubiquiti"]) == {"111", "222"}


def test_present_players_ignores_absent_ones():
    demo = make_demo([], [], slots={"111": 1})
    demo.player_names = {"111": "Ann", "222": "Bob"}
    found, missing = present_players(demo, ["Ann,Zed"], {"222", "999"})
    assert found == {"111", "222"} and missing == ["Zed"]


@pytest.fixture
def batch_env(tmp_path, monkeypatch):
    def demo(name, kills, rounds, players):
        d = make_demo(kills, rounds)
        d.path = Path(name)
        d.player_names = players
        return d

    rounds = [make_round(0, 0, 100000, winner="CT")]
    kills_a = [
        make_kill(2000, attacker="ann", victim="v1", headshot=True),
        make_kill(3000, attacker="ann", victim="v2", headshot=True),
        make_kill(30000, attacker="ann", victim="v1"),
        make_kill(31000, attacker="ann", victim="v2"),
        make_kill(60000, attacker="bob", victim="v1"),
        make_kill(61000, attacker="bob", victim="v2"),
    ]
    kills_b = [make_kill(2000, attacker="cat", victim="v1"), make_kill(3000, attacker="cat", victim="v2")]
    demos = {
        "a.dem": demo("a.dem", kills_a, rounds, {"ann": "Ann", "bob": "Bob"}),
        "b.dem": demo("b.dem", kills_b, rounds, {"cat": "Cat"}),
    }
    monkeypatch.setattr(cli, "parse_demo", lambda path: demos[Path(path).name])
    monkeypatch.setattr(cli, "ensure_dem_file", lambda path, work_dir: path)
    monkeypatch.setattr(cli, "load_config", lambda: Config())
    monkeypatch.setattr(cli, "_resolve_work_dir", lambda config, demo_path: tmp_path / "work" / demo_path.stem)
    files = {}
    for name in demos:
        files[name] = tmp_path / name
        files[name].write_bytes(b"")
    return click.testing.CliRunner(), files, tmp_path / "out"


def _run(env, *args):
    runner, files, out = env
    return runner.invoke(
        cli.main,
        ["batch", f"match07={files['a.dem']}", f"match08={files['b.dem']}", "-o", str(out), "--json-only", *args],
    )


def test_batch_picks_best_clips_globally_one_file_per_highlight(batch_env):
    result = _run(batch_env, "--top", "3")
    assert result.exit_code == 0, result.output
    out = batch_env[2]
    names = sorted(p.stem for p in out.glob("*.json"))
    assert len(names) == 3 and all(n.startswith(("match07-", "match08-")) for n in names)
    assert len(set(names)) == 3
    report = json.loads((out / f"{names[0]}.json").read_text())
    assert [s["number"] for s in report["sequences"]] == [1]
    ranks = sorted(json.loads((out / f"{n}.json").read_text())["batch"]["rank"] for n in names)
    assert ranks == [1, 2, 3]
    best = next(json.loads((out / f"{n}.json").read_text())["batch"] for n in names
                if json.loads((out / f"{n}.json").read_text())["batch"]["rank"] == 1)
    assert best["relative_score"] == 1.0 and best["of"] == 3


def test_batch_player_filter_skips_demos_without_them(batch_env):
    result = _run(batch_env, "--top", "5", "--players", "Ann,Zed")
    assert result.exit_code == 0, result.output
    names = sorted(p.stem for p in batch_env[2].glob("*.json"))
    assert names and all(n.startswith("match07-playerann-") for n in names)
    assert "none of the players are in this demo" in result.output


def test_batch_top_is_global_not_per_player(batch_env):
    result = _run(batch_env, "--top", "1", "--players", "Ann,Bob")
    assert result.exit_code == 0, result.output
    assert len(list(batch_env[2].glob("*.json"))) == 1


def test_batch_rejects_duplicate_labels_and_missing_names_file(batch_env):
    runner, files, out = batch_env
    result = runner.invoke(cli.main, ["batch", f"x={files['a.dem']}", f"x={files['b.dem']}", "-o", str(out)])
    assert result.exit_code != 0 and "share the label" in result.output
    result = runner.invoke(cli.main, ["batch", str(files["a.dem"]), "--team", "Saltcrew", "-o", str(out)])
    assert result.exit_code != 0 and "--names-file" in result.output


def test_batch_takes_folders_recursively(batch_env):
    runner, files, out = batch_env
    sub = files["b.dem"].parent / "week2"
    sub.mkdir()
    files["b.dem"].rename(sub / "b.dem")
    folder = str(files["a.dem"].parent)
    flat = runner.invoke(cli.main, ["batch", folder, "-o", str(out), "--json-only", "--top", "10"])
    assert flat.exit_code == 0, flat.output
    assert {p.stem.split("-")[0] for p in out.glob("*.json")} == {"a"}
    deep = runner.invoke(cli.main, ["batch", folder, "--recursive", "-o", str(out), "--json-only", "--top", "10"])
    assert deep.exit_code == 0, deep.output
    assert {p.stem.split("-")[0] for p in out.glob("*.json")} == {"a", "b"}
    empty = files["a.dem"].parent / "empty"
    empty.mkdir()
    result = runner.invoke(cli.main, ["batch", str(empty), "-o", str(out)])
    assert result.exit_code != 0 and "No .dem files" in result.output


def test_round_rules_parse_and_filter():
    import pytest

    from highlights.analysis.selection import parse_rounds, round_allowed

    rules = parse_rounds(["13", "semi-finals:2, 7"])
    assert rules == [(None, 13), ("semi-finals", 2), ("semi-finals", 7)]
    assert round_allowed(rules, "grand-finals", 12) and not round_allowed(rules, "grand-finals", 1)
    assert round_allowed(rules, "Semi-Finals", 1) and not round_allowed(rules, "semi-finals", 2)
    assert round_allowed([], "any", 0)
    for bad in ("x", "0", ":3", "a:b"):
        with pytest.raises(ValueError):
            parse_rounds([bad])


def test_batch_per_kill_writes_one_clip_per_kill(batch_env):
    result = _run(batch_env, "--top", "10", "--players", "Ann", "--per-kill", "--headshot-only")
    assert result.exit_code == 0, result.output
    reports = [json.loads(p.read_text()) for p in sorted(batch_env[2].glob("*.json"))]
    assert len(reports) == 2
    for report in reports:
        assert report["batch"]["kill_filter"] == {"weapons": [], "headshot_only": True, "per_kill": True}
        sequence = report["sequences"][0]
        assert (sequence["end_tick"] - sequence["start_tick"]) / 64 == 3.5
