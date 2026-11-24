"""Tests for --suggest-cinematic: the suggestions table and the JSON key."""

from pathlib import Path

import click.testing
import pytest
from conftest import make_demo, make_kill, make_round
from rich.console import Console

import highlights.cli as cli
import highlights.places as places
from highlights.config import Config
from highlights.models import CameraAction, Moment, Sequence
from highlights.report import print_suggestions_table, write_report

TICKRATE = 64.0


def _sequence(number=1, label="Clutch", start=0, end=6400):
    return Sequence(
        number=number,
        start_tick=start,
        end_tick=end,
        cameras=[CameraAction(tick=start, steamid="111")],
        label=label,
        score=7.0,
        round_index=0,
    )


def _demo():
    return make_demo([], [make_round(0, 0, 100000)])


def _best(name="A4", label="Balcony", coverage=11.2, route=("Catwalk", "A Main"), overlap=0.5):
    return {
        "name": name,
        "label": label,
        "distance": 120.0,
        "coverage_seconds": coverage,
        "route": list(route),
        "route_overlap": overlap,
    }


def _suggestion(verdict="good", best=None, alternatives=None, why=("3K", "path passes A Main")):
    return {
        "verdict": verdict,
        "why": list(why),
        "clip_route": "Spawn \u2192 A Site",
        "best": best,
        "alternatives": alternatives or [],
    }


def test_suggestions_table_renders_fit_path_route_and_also(monkeypatch):
    """Fit, path name+label, coverage, route and the also: line all appear."""
    sequence = _sequence()
    suggestions = {
        1: _suggestion(
            best=_best(),
            alternatives=[
                {"name": "A5", "label": None, "distance": 200.0, "coverage_seconds": 1.8, "route": [], "route_overlap": 0.0},
                {"name": "A6", "label": None, "distance": 260.0, "coverage_seconds": None, "route": [], "route_overlap": 0.0},
            ],
        )
    }
    recording = Console(record=True, width=200)
    monkeypatch.setattr("highlights.report.console", recording)
    print_suggestions_table(_demo(), [sequence], suggestions)
    output = recording.export_text()
    assert "Cinematic suggestions" in output
    assert "good" in output
    assert "A4" in output and "Balcony" in output
    assert "sees player 11.2s" in output
    assert "Catwalk > A Main" in output
    assert "also: A5 1.8s, A6" in output
    assert "3K" in output


def test_suggestions_table_escapes_brackets_and_shows_dash(monkeypatch):
    """Square brackets in names survive rich markup; a missing best renders '-'."""
    sequence = _sequence()
    suggestions = {1: _suggestion(verdict="none", best=_best(name="B[5]", label="Pit["), why=["no cam path"])}
    recording = Console(record=True, width=200)
    monkeypatch.setattr("highlights.report.console", recording)
    print_suggestions_table(_demo(), [sequence], suggestions)
    assert "B[5]" in recording.export_text()

    suggestions = {1: _suggestion(verdict="none", best=None, why=["no position data"])}
    recording = Console(record=True, width=200)
    monkeypatch.setattr("highlights.report.console", recording)
    print_suggestions_table(_demo(), [sequence], suggestions)
    assert "-" in recording.export_text()


def test_write_report_adds_cinematic_suggestion_key(tmp_path):
    """With suggestions each sequence gains cinematic_suggestion; without, no key."""
    path = tmp_path / "out.json"
    sequences = [_sequence(1), _sequence(2, label="Other")]
    suggestions = {1: _suggestion(best=_best())}  # sequence 2 missing -> None
    write_report(path, _demo(), sequences, spatial_by_sequence={}, suggestions=suggestions)
    import json

    data = json.loads(path.read_text())
    assert data["sequences"][0]["cinematic_suggestion"]["verdict"] == "good"
    assert data["sequences"][1]["cinematic_suggestion"] is None

    path = tmp_path / "plain.json"
    write_report(path, _demo(), sequences, spatial_by_sequence={})
    data = json.loads(path.read_text())
    assert all("cinematic_suggestion" not in s for s in data["sequences"])


# --- CLI wiring -------------------------------------------------------------

CAMPATH_XML = """<?xml version="1.0"?>
<campath>
  <points>
    <p t="0" x="0" y="0" z="0" rx="0" ry="0" rz="0" qw="1" qx="0" qy="0" qz="0" fov="90"/>
    <p t="1" x="10" y="0" z="0" rx="0" ry="0" rz="0" qw="1" qx="0" qy="0" qz="0" fov="90"/>
  </points>
</campath>
"""


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    """Run `highlights run` on a tiny double-kill demo with cam paths in tmp_path."""
    alice, v1, v2 = "alice", "v1", "v2"
    kills = [
        make_kill(2000, round_index=0, attacker=alice, victim=v1),
        make_kill(3000, round_index=0, attacker=alice, victim=v2),
    ]
    demo = make_demo(kills, [make_round(0, 0, 100000, winner="CT")])
    demo.path = tmp_path / "m.dem"
    demo.map_name = "de_test"
    demo.tickrate = TICKRATE
    demo.path.write_bytes(b"")

    monkeypatch.setattr(cli, "parse_demo", lambda path: demo)
    monkeypatch.setattr(cli, "ensure_dem_file", lambda path, work_dir: path)
    config = Config()
    campaths_dir = tmp_path / "campaths"
    (campaths_dir / "de_test").mkdir(parents=True)
    (campaths_dir / "de_test" / "A4.xml").write_text(CAMPATH_XML, encoding="utf-8")
    config.paths.campaths_dir = str(campaths_dir)
    monkeypatch.setattr(cli, "load_config", lambda: config)
    monkeypatch.setattr(cli, "_resolve_work_dir", lambda config, demo_path: tmp_path)
    monkeypatch.setattr("highlights.spatial._query_positions", lambda path, ticks: ({}, [], None))
    monkeypatch.setattr(places, "app_data_dir", lambda: tmp_path)
    return tmp_path


def _invoke(tmp_path, *extra):
    return click.testing.CliRunner().invoke(
        cli.main,
        ["run", str(tmp_path / "m.dem"), "--suggest-cinematic", "--json-only", "-o", str(tmp_path / "out.mp4"), *extra],
    )


def test_suggest_with_cinematic_is_a_usage_error(cli_env):
    """--suggest-cinematic together with --cinematic exits non-zero with the hint."""
    result = _invoke(cli_env, "--cinematic")
    assert result.exit_code != 0
    assert "--suggest-cinematic is for previewing; use it without --cinematic" in result.output


def test_suggest_prints_table_and_writes_key(cli_env, monkeypatch):
    """--suggest-cinematic prints the table and stores the suggestion in the JSON."""
    fixed = {1: _suggestion(verdict="good", best=_best())}
    monkeypatch.setattr("highlights.recording.suggest.suggest_cinematics", lambda *a, **k: fixed)
    result = _invoke(cli_env)
    assert result.exit_code == 0, result.output
    assert "Cinematic suggestions" in result.output
    import json

    data = json.loads((cli_env / "out.json").read_text())
    assert data["sequences"][0]["cinematic_suggestion"]["verdict"] == "good"


def test_run_without_flag_never_calls_suggest(cli_env, monkeypatch):
    """A plain run has no cinematic_suggestion key and never calls suggest_cinematics."""

    def boom(*a, **k):
        raise AssertionError("suggest_cinematics must not run")

    monkeypatch.setattr("highlights.recording.suggest.suggest_cinematics", boom)
    result = click.testing.CliRunner().invoke(
        cli.main,
        ["run", str(cli_env / "m.dem"), "--json-only", "-o", str(cli_env / "out.mp4")],
    )
    assert result.exit_code == 0, result.output
    import json

    data = json.loads((cli_env / "out.json").read_text())
    assert all("cinematic_suggestion" not in s for s in data["sequences"])
