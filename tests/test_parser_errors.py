"""Hardened error handling: demoparser2 panics and bad input never escape as tracebacks."""

from pathlib import Path

import click.testing
import pytest
from conftest import make_demo, make_kill, make_round

import highlights.cli as cli
import highlights.spatial as spatial
from highlights.config import Config
from highlights.demo import parser as parser_module
from highlights.demo.parser import DemoParseError, is_parser_error
from highlights.models import CameraAction, Sequence


def _panic():
    """A stand-in for pyo3_runtime.PanicException (derives from BaseException)."""
    fake = type("PanicException", (BaseException,), {})
    fake.__module__ = "pyo3_runtime"
    return fake("index out of bounds: the len is 0 but the index is 0")


# --- a. is_parser_error -------------------------------------------------------


def test_is_parser_error_accepts_exceptions_and_rust_panic():
    assert is_parser_error(ValueError("boom"))
    assert is_parser_error(Exception("boom"))
    assert is_parser_error(_panic())


def test_is_parser_error_rejects_control_flow_exceptions():
    assert not is_parser_error(KeyboardInterrupt())
    assert not is_parser_error(SystemExit())
    assert not is_parser_error(GeneratorExit())

    class OtherPanicLike(BaseException):
        pass

    OtherPanicLike.__name__ = "NotAPanic"
    OtherPanicLike.__module__ = "somewhere_else"
    assert not is_parser_error(OtherPanicLike())


# --- b. parse_demo on bad files ----------------------------------------------


def test_parse_demo_missing_file(tmp_path):
    with pytest.raises(DemoParseError, match="not found"):
        parser_module.parse_demo(tmp_path / "nope.dem")


def test_parse_demo_empty_file(tmp_path):
    dem = tmp_path / "empty.dem"
    dem.write_bytes(b"")
    with pytest.raises(DemoParseError, match="empty"):
        parser_module.parse_demo(dem)


def test_parse_demo_random_bytes(tmp_path):
    dem = tmp_path / "junk.dem"
    dem.write_bytes(bytes(range(256)) * 40)
    with pytest.raises(DemoParseError):
        parser_module.parse_demo(dem)


# --- c. parse_demo vs. a panicking parser ------------------------------------


def _fake_panic_parser_cls(exc_factory):
    class FakeParser:
        def __init__(self, path):
            pass

        def parse_header(self):
            raise exc_factory()

        def parse_event(self, name, **kwargs):
            raise exc_factory()

    return FakeParser


def test_parse_demo_converts_rust_panic(monkeypatch, tmp_path):
    monkeypatch.setattr(parser_module, "DemoParser", _fake_panic_parser_cls(_panic))
    dem = tmp_path / "fake.dem"
    with pytest.raises(DemoParseError):
        parser_module.parse_demo(dem)


def test_parse_demo_lets_keyboard_interrupt_through(monkeypatch, tmp_path):
    monkeypatch.setattr(parser_module, "DemoParser", _fake_panic_parser_cls(KeyboardInterrupt))
    dem = tmp_path / "fake.dem"
    dem.write_bytes(b"not empty")
    with pytest.raises(KeyboardInterrupt):
        parser_module.parse_demo(dem)


# --- d. build_spatial_metadata vs. a panicking position query ----------------


def _sequence(number: int, start: int, end: int, steamid: str = "111") -> Sequence:
    return Sequence(
        number=number,
        start_tick=start,
        end_tick=end,
        cameras=[CameraAction(tick=start, steamid=steamid)],
        label="test",
        score=1.0,
        round_index=0,
    )


def test_build_spatial_metadata_reports_panic_as_unavailable(monkeypatch):
    def boom(path, ticks):
        raise _panic()

    monkeypatch.setattr(spatial, "_query_positions", boom)
    rounds = [make_round(0, 0, 100000)]
    demo = make_demo([make_kill(5000, attacker="111")], rounds)
    demo.path = Path("does-not-exist.dem")  # never opened: _query_positions is patched
    sequences = [_sequence(1, 5000, 6000), _sequence(2, 20000, 21000)]

    result = spatial.build_spatial_metadata(demo, sequences)

    assert set(result) == {1, 2}
    for meta in result.values():
        assert meta["status"] == "unavailable"
        assert "PanicException" in meta["error"]


# --- e. sample_places ---------------------------------------------------------


def test_sample_places_panic_becomes_demo_parse_error(monkeypatch, tmp_path):
    class FakeParser:
        def __init__(self, path):
            pass

        def parse_ticks(self, props, ticks):
            raise _panic()

    monkeypatch.setattr(spatial, "DemoParser", FakeParser)
    with pytest.raises(DemoParseError, match="could not sample places"):
        spatial.sample_places(tmp_path / "m.dem", [1, 2, 3])


def test_sample_places_empty_ticks_uses_no_parser(monkeypatch, tmp_path):
    class ExplodingParser:
        def __init__(self, path):
            raise AssertionError("parser must not be built for an empty tick list")

    monkeypatch.setattr(spatial, "DemoParser", ExplodingParser)
    assert spatial.sample_places(tmp_path / "m.dem", []) == []


# --- f. CLI behaviour ----------------------------------------------------------


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "ensure_dem_file", lambda path, work_dir: path)
    monkeypatch.setattr(cli, "load_config", lambda: Config())
    monkeypatch.setattr(cli, "_resolve_work_dir", lambda config, demo_path: tmp_path)
    return tmp_path, click.testing.CliRunner()


def test_cli_run_demo_parse_error_is_clean_message(cli_env, monkeypatch):
    tmp_path, runner = cli_env
    dem = tmp_path / "x.dem"
    dem.write_bytes(b"")

    def boom(path):
        raise DemoParseError("x.dem: file is empty")

    monkeypatch.setattr(cli, "parse_demo", boom)
    result = runner.invoke(cli.main, [str(dem), "--json-only", "-o", str(tmp_path / "out.mp4")])
    assert result.exit_code != 0
    assert "x.dem: file is empty" in result.output
    assert "Traceback" not in result.output


def _synthetic_demo(name: str):
    rounds = [make_round(0, 0, 100000, winner="CT")]
    kills = [
        make_kill(2000, round_index=0, attacker="alice", victim="v1"),
        make_kill(3000, round_index=0, attacker="alice", victim="v2"),
    ]
    demo = make_demo(kills, rounds)
    demo.path = Path(name)
    demo.map_name = "de_mirage"
    return demo


def test_cli_top_skips_failed_demo_and_prints_table(cli_env, monkeypatch):
    tmp_path, runner = cli_env
    bad = tmp_path / "bad.dem"
    good = tmp_path / "good.dem"
    bad.write_bytes(b"")
    good.write_bytes(b"")
    synthetic = _synthetic_demo("good.dem")

    def fake_parse(path):
        if Path(path).name == "bad.dem":
            raise DemoParseError("bad.dem: could not be read (PanicException: boom) — is it a complete CS2 demo?")
        return synthetic

    monkeypatch.setattr(cli, "parse_demo", fake_parse)
    result = runner.invoke(cli.main, ["top", str(bad), str(good)])
    assert result.exit_code == 0, result.output
    assert "bad.dem" in result.output and "skipping" in result.output
    assert "playeralice" in result.output  # the table was printed


def test_cli_top_all_demos_fail(cli_env, monkeypatch):
    tmp_path, runner = cli_env
    a = tmp_path / "a.dem"
    b = tmp_path / "b.dem"
    a.write_bytes(b"")
    b.write_bytes(b"")

    def boom(path):
        raise DemoParseError(f"{Path(path).name}: file is empty")

    monkeypatch.setattr(cli, "parse_demo", boom)
    result = runner.invoke(cli.main, ["top", str(a), str(b)])
    assert result.exit_code != 0
    assert "None of the demos could be read" in result.output


def test_cli_places_build_skips_failed_demo(cli_env, monkeypatch):
    import highlights.places as places

    tmp_path, runner = cli_env
    monkeypatch.setattr(places, "app_data_dir", lambda: tmp_path)
    bad = tmp_path / "bad.dem"
    good = tmp_path / "good.dem"
    bad.write_bytes(b"")
    good.write_bytes(b"")
    synthetic = _synthetic_demo("good.dem")
    synthetic.last_tick = 1000
    synthetic.tickrate = 64.0

    def fake_parse(path):
        if Path(path).name == "bad.dem":
            raise DemoParseError("bad.dem: file is empty")
        return synthetic

    monkeypatch.setattr(cli, "parse_demo", fake_parse)
    monkeypatch.setattr(
        cli, "sample_places", lambda path, ticks: [(100.0, 200.0, 50.0, "Catwalk")]
    )
    result = runner.invoke(cli.main, ["places", "build", str(bad), str(good)])
    assert result.exit_code == 0, result.output
    assert "bad.dem" in result.output and "skipping" in result.output
    assert (tmp_path / "places" / "de_mirage.json").exists()


def test_unreadable_kills_point_at_a_demoparser2_update(monkeypatch, tmp_path):
    import pandas as pd

    class OutdatedParser:
        def __init__(self, path):
            pass

        def parse_header(self):
            return {"map_name": "de_nuke"}

        def parse_event(self, name, **kwargs):
            if name == "player_death":
                raise Exception("EntityNotFound")  # what demoparser2 0.42.0 raised after a CS2 update
            return pd.DataFrame()

    monkeypatch.setattr(parser_module, "DemoParser", OutdatedParser)
    dem = tmp_path / "match.dem"
    dem.write_bytes(b"PBDEMS2\0")
    with pytest.raises(DemoParseError, match="newer demoparser2"):
        parser_module.parse_demo(dem)
