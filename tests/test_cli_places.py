from pathlib import Path

import click.testing
import pytest
from conftest import make_demo, make_kill, make_round

import highlights.cli as cli
import highlights.places as places
from highlights.config import Config


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Redirect place storage into tmp_path and stub out demo parsing."""
    monkeypatch.setattr(places, "app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(cli, "load_config", lambda: Config())
    monkeypatch.setattr(cli, "_resolve_work_dir", lambda config, demo_path: tmp_path)
    monkeypatch.setattr(cli, "ensure_dem_file", lambda path, work_dir: path)
    return tmp_path, click.testing.CliRunner()


def _demo(name: str, map_name: str, last_tick: int = 1000) -> Path:
    kills = [make_kill(2000, round_index=0, attacker="alice", victim="v1")]
    demo = make_demo(kills, [make_round(0, 0, last_tick - 640)])
    demo.path = Path(name)
    demo.map_name = map_name
    demo.last_tick = last_tick
    demo.tickrate = 64.0
    DEMOS[Path(name).name] = demo
    file = Path(name)
    file.write_bytes(b"")
    return file


DEMOS: dict[str, object] = {}


def _install(monkeypatch, samples_by_map):
    """parse_demo returns the registered demo for a file; sample_places returns canned samples per map."""
    monkeypatch.setattr(cli, "parse_demo", lambda path: DEMOS[Path(path).name])

    captured: dict[str, list] = {}

    def fake_sample(demo_path, ticks):
        captured.setdefault(Path(demo_path).name, []).append(list(ticks))
        return samples_by_map.get(DEMOS[Path(demo_path).name].map_name, [])

    monkeypatch.setattr(cli, "sample_places", fake_sample)
    return captured


def _build(runner, files, *extra):
    return runner.invoke(cli.main, ["places", "build", *[str(f) for f in files], *extra])


def test_build_writes_place_map(env, monkeypatch):
    tmp_path, runner = env
    demo = _demo("a.dem", "de_mirage")
    samples = {"de_mirage": [(100.0, 200.0, 50.0, "Catwalk"), (104.0, 204.0, 50.0, "Catwalk")]}
    _install(monkeypatch, samples)

    result = _build(runner, [demo])
    assert result.exit_code == 0, result.output

    path = tmp_path / "places" / "de_mirage.json"
    assert path.exists()
    saved = places.load_places("de_mirage")
    assert saved is not None
    assert saved.lookup(100.0, 200.0, 50.0) == "Catwalk"
    assert "de_mirage" in result.output
    assert "1" in result.output and "cells" in result.output


def test_build_accumulates_across_invocations_and_demos(env, monkeypatch):
    tmp_path, runner = env
    a1 = _demo("a1.dem", "de_mirage")
    a2 = _demo("a2.dem", "de_mirage")

    # First invocation: Mid is the most common place.
    _install(
        monkeypatch,
        {"de_mirage": [(100.0, 200.0, 50.0, "Mid"), (100.0, 200.0, 50.0, "Mid"), (100.0, 200.0, 50.0, "Catwalk")]},
    )
    result = _build(runner, [a1])
    assert result.exit_code == 0, result.output
    assert places.load_places("de_mirage").lookup(100.0, 200.0, 50.0) == "Mid"

    # Second invocation, two demos of the same map: enough Catwalk votes flip the cell.
    _install(
        monkeypatch,
        {
            "de_mirage": [
                (100.0, 200.0, 50.0, "Catwalk"),
                (100.0, 200.0, 50.0, "Catwalk"),
                (100.0, 200.0, 50.0, "Catwalk"),
                (100.0, 200.0, 50.0, "Catwalk"),
            ]
        },
    )
    result = _build(runner, [a1, a2])
    assert result.exit_code == 0, result.output
    saved = places.load_places("de_mirage")
    assert saved.lookup(100.0, 200.0, 50.0) == "Catwalk"
    # Accumulated: Mid's first-round votes plus a Catwalk remain counted in that cell.
    assert saved.to_dict()["cells"]["1,3,0"] == {"Mid": 2, "Catwalk": 9}


def test_build_two_maps_in_one_invocation(env, monkeypatch):
    tmp_path, runner = env
    m = _demo("m.dem", "de_mirage")
    i = _demo("i.dem", "de_inferno")
    _install(
        monkeypatch,
        {
            "de_mirage": [(100.0, 200.0, 50.0, "Catwalk")],
            "de_inferno": [(500.0, 600.0, 30.0, "Banana")],
        },
    )
    result = _build(runner, [m, i])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "places" / "de_mirage.json").exists()
    assert (tmp_path / "places" / "de_inferno.json").exists()


def test_build_every_must_be_positive(env, monkeypatch):
    _, runner = env
    m = _demo("m.dem", "de_mirage")
    _install(monkeypatch, {"de_mirage": [(1.0, 2.0, 3.0, "A")]})
    assert _build(runner, [m], "--every", "0").exit_code != 0
    assert _build(runner, [m], "--every", "-1").exit_code != 0


def test_build_tick_spacing_uses_every(env, monkeypatch):
    _, runner = env
    m = _demo("m.dem", "de_mirage", last_tick=1000)
    captured = _install(monkeypatch, {"de_mirage": [(1.0, 2.0, 3.0, "A")]})

    result = _build(runner, [m], "--every", "2")
    assert result.exit_code == 0, result.output
    ticks = captured["m.dem"][0]
    step = round(64.0 * 2)
    assert ticks[0] == 1
    assert ticks[1] - ticks[0] == step
    assert all(b - a == step for a, b in zip(ticks, ticks[1:]))
    assert ticks[-1] <= 1000


def test_build_zero_samples_warns_and_writes_nothing(env, monkeypatch):
    tmp_path, runner = env
    m = _demo("m.dem", "de_mirage")
    _install(monkeypatch, {})
    result = _build(runner, [m])
    assert result.exit_code == 0, result.output
    assert "Warning" in result.output
    assert not (tmp_path / "places" / "de_mirage.json").exists()


def test_list_empty_shows_hint_then_lists_maps(env, monkeypatch):
    tmp_path, runner = env
    result = runner.invoke(cli.main, ["places", "list"])
    assert result.exit_code == 0, result.output
    assert "highlights places build" in result.output

    m = _demo("m.dem", "de_mirage")
    _install(monkeypatch, {"de_mirage": [(100.0, 200.0, 50.0, "A"), (300.0, 300.0, 50.0, "B")]})
    assert _build(runner, [m]).exit_code == 0

    result = runner.invoke(cli.main, ["places", "list"])
    assert result.exit_code == 0, result.output
    assert "de_mirage" in result.output
    assert "2" in result.output  # cell count


def test_top_build_places_only_builds_missing_maps(env, monkeypatch):
    tmp_path, runner = env
    mirage1, mirage2 = _demo("m1.dem", "de_mirage"), _demo("m2.dem", "de_mirage")
    nuke = _demo("n.dem", "de_nuke")
    existing = places.PlaceMap("de_nuke")
    existing.add(0.0, 0.0, 0.0, "Outside")
    places.save_places(existing)
    captured = _install(monkeypatch, {"de_mirage": [(100.0, 200.0, 50.0, "Catwalk")], "de_nuke": [(0.0, 0.0, 0.0, "Ramp")]})

    result = runner.invoke(cli.main, ["top", str(mirage1), str(mirage2), str(nuke), "--min-kills", "1", "--build-places"])
    assert result.exit_code == 0, result.output
    assert sorted(captured) == ["m1.dem", "m2.dem"]  # both demos of the missing map, none of nuke
    assert places.load_places("de_mirage").lookup(100.0, 200.0, 50.0) == "Catwalk"
    assert places.load_places("de_nuke").lookup(0.0, 0.0, 0.0) == "Outside"  # untouched

    captured.clear()
    again = runner.invoke(cli.main, ["top", str(mirage1), "--min-kills", "1", "--build-places"])
    assert again.exit_code == 0 and captured == {} and "already has a place map" in again.output


def test_top_without_the_flag_samples_nothing(env, monkeypatch):
    _, runner = env
    demo = _demo("x.dem", "de_mirage")
    captured = _install(monkeypatch, {"de_mirage": [(1.0, 1.0, 1.0, "Mid")]})
    assert runner.invoke(cli.main, ["top", str(demo), "--min-kills", "1"]).exit_code == 0
    assert captured == {} and places.load_places("de_mirage") is None


def test_top_rebuild_places_replaces_saved_maps(env, monkeypatch):
    _, runner = env
    nuke1, nuke2 = _demo("n1.dem", "de_nuke"), _demo("n2.dem", "de_nuke")
    old = places.PlaceMap("de_nuke")
    old.add(5000.0, 5000.0, 0.0, "Gone")
    places.save_places(old)
    captured = _install(monkeypatch, {"de_nuke": [(0.0, 0.0, 0.0, "Ramp")]})

    result = runner.invoke(cli.main, ["top", str(nuke1), str(nuke2), "--min-kills", "1", "--rebuild-places"])
    assert result.exit_code == 0, result.output
    assert sorted(captured) == ["n1.dem", "n2.dem"] and "Rebuilt place map" in result.output
    rebuilt = places.load_places("de_nuke")
    assert rebuilt.lookup(5000.0, 5000.0, 0.0) is None and rebuilt.lookup(0.0, 0.0, 0.0) == "Ramp"
