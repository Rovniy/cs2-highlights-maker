import json
import shutil
import subprocess
from pathlib import Path

import click.testing
import pytest
from conftest import make_demo, make_kill, make_round

import highlights.cli as cli
from highlights.analysis.slowmo import (
    SPEED,
    SlowmoPick,
    best_kill,
    choose,
    extra_seconds,
    plan_sequences,
    remap,
    window,
)
from highlights.config import Config
from highlights.models import CameraAction, Clutch, Moment, Sequence
from highlights.report import finalize_report
from highlights.video.slowmo import atempo_chain, slow_clip, slowmo_filter


def _moment(weapons=("ak47", "ak47"), **kill_flags):
    kills = [make_kill(1000 + i * 100, weapon=w, **(kill_flags if i == len(weapons) - 1 else {})) for i, w in enumerate(weapons)]
    return Moment(steamid="111", name="p", side="CT", round_index=0, kills=kills, score=5.0)


def test_plain_kills_never_get_slowmo():
    assert best_kill(_moment()) is None
    assert best_kill(_moment(headshot=True)) is None


def test_special_kill_is_picked_and_reason_named():
    pick = best_kill(_moment(weapons=("ak47", "knife")))
    assert pick.kill.weapon == "knife" and "knife" in pick.reason
    pick = best_kill(_moment(weapons=("awp", "awp"), noscope=True))
    assert "noscope" in pick.reason


def test_ace_and_clutch_finish_count_on_the_last_kill():
    ace = best_kill(_moment(weapons=("ak47",) * 5))
    assert ace.kill.tick == 1400 and "ace finish" in ace.reason
    assert best_kill(_moment(weapons=("ak47",) * 3)) is None
    clutch = _moment(weapons=("ak47", "ak47"))
    clutch.clutch = Clutch("111", "p", "CT", 2, True, 2, 900, 5000, 0)
    assert "1v2 clutch finish" in best_kill(clutch).reason


def test_choose_keeps_only_the_best_third_rounded_up():
    kill = make_kill(1)
    picks = [SlowmoPick(kill, float(w), "x") for w in (3, 9, 5, 4, 8, 3, 6, 7, 3, 4)]
    kept = choose(picks)
    assert {p.worth for p in kept if p} == {6.0, 7.0, 8.0, 9.0}
    assert choose([None, None]) == [None, None]


def test_window_and_remap():
    start, end = window(5.0, 20.0)
    assert (start, end) == (4.3, 6.1)
    assert extra_seconds(start, end) == pytest.approx(1.8 * 1.5)
    assert remap(2.0, start, end) == 2.0
    assert remap(5.2, start, end) == pytest.approx(4.3 + 0.9 / SPEED)
    assert remap(10.0, start, end) == pytest.approx(10.0 + extra_seconds(start, end))
    assert window(0.2, 3.0) == (0.0, 1.3)


def _sequence(number, kill_weapon="knife", cinematic_of=None):
    moment = _moment(weapons=("ak47", kill_weapon))
    return Sequence(
        number=number,
        start_tick=0,
        end_tick=64 * 20,
        cameras=[CameraAction(0, "111")],
        label="x",
        score=5.0,
        round_index=0,
        moments=[moment],
        cinematic_of=cinematic_of,
    )


def test_plan_sequences_skips_cinematic_takes_and_respects_budget():
    sequences = [_sequence(1), _sequence(2, cinematic_of=1), _sequence(3, "ak47")]
    planned = plan_sequences(sequences, 64.0)
    assert planned[0].slowmo and planned[0].slowmo["speed"] == SPEED
    assert planned[1].slowmo is None and planned[2].slowmo is None
    # 20 s of clips plus the extra seconds must stay within max_duration
    assert plan_sequences(sequences, 64.0, max_duration=20.5)[0].slowmo is None
    assert plan_sequences(sequences, 64.0, max_duration=60)[0].slowmo is not None


def test_report_remaps_kill_times_through_the_slowmo_window(tmp_path):
    path = tmp_path / "r.json"
    ev = lambda t: {  # noqa: E731
        "type": "kill", "seconds_from_clip_start": t, "tick": 0, "weapon": "ak47", "headshot": False,
        "noscope": False, "thrusmoke": False, "attackerblind": False, "penetrated": 0,
        "victim": {"name": "v"}, "is_pov_kill": True, "is_selected_moment_kill": True,
    }
    slowmo = {"start_seconds": 4.3, "end_seconds": 6.1, "speed": 0.4}
    path.write_text(json.dumps({"editing": {}, "sequences": [
        {"number": 1, "slowmo": slowmo, "events": [ev(2.0), ev(5.0), ev(10.0)]},
        {"number": 2, "events": [ev(1.0)]},
    ]}))
    finalize_report(path, {1: 22.7, 2: 10.0})
    times = [t["video_time_seconds"] for t in json.loads(path.read_text())["beat_targets"]]
    assert times == [pytest.approx(2.0), pytest.approx(4.3 + 0.7 / 0.4), pytest.approx(12.7), pytest.approx(23.7)]


def test_filter_structure():
    assert atempo_chain(0.4) == "atempo=0.5,atempo=0.8000"
    assert atempo_chain(0.75) == "atempo=0.7500"
    graph = slowmo_filter(4.3, 6.1, 0.4)
    assert "concat=n=3" in graph and "/0.4000" in graph
    assert "concat=n=2" in slowmo_filter(0.0, 1.0, 0.4)  # nothing before the window


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_slow_clip_gets_longer_by_the_extra_seconds(tmp_path):
    src = tmp_path / "s.mp4"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=30:duration=4",
         "-f", "lavfi", "-i", "sine=duration=4", "-shortest", "-pix_fmt", "yuv420p", str(src)],
        check=True,
    )
    out = slow_clip(Path(shutil.which("ffmpeg")), src, tmp_path / "o.mp4", 1.0, 2.0, 0.4)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True,
    )
    assert float(probe.stdout) == pytest.approx(4.0 + extra_seconds(1.0, 2.0), abs=0.15)


def test_top_slow_mode_marks_the_clips_worth_it(tmp_path, monkeypatch):
    rounds = [make_round(0, 0, 100000, winner="CT")]
    kills = [
        make_kill(2000, attacker="ann", victim="v1"),
        make_kill(3000, attacker="ann", victim="v2", weapon="knife"),
        make_kill(5000, attacker="bob", victim="v1"),
        make_kill(6000, attacker="bob", victim="v2"),
    ]
    demo = make_demo(kills, rounds)
    demo.path = Path("a.dem")
    monkeypatch.setattr(cli, "parse_demo", lambda path: demo)
    monkeypatch.setattr(cli, "ensure_dem_file", lambda path, work_dir: path)
    monkeypatch.setattr(cli, "load_config", lambda: Config())
    monkeypatch.setattr(cli, "_resolve_work_dir", lambda config, demo_path: tmp_path)
    dem = tmp_path / "a.dem"
    dem.write_bytes(b"")
    runner = click.testing.CliRunner()
    result = runner.invoke(cli.main, ["top", str(dem), "--slow-mode", "auto"], env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    assert "Slow-mo" in result.output and "kill 2/2: knife" in result.output
    assert "Slow-motion for 1 of 2 clips" in result.output
    plain = runner.invoke(cli.main, ["top", str(dem)])
    assert "Slow-mo" not in plain.output
