import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner
from conftest import make_demo, make_kill, make_round
from test_campaths import _sequence

from highlights.cli import _splice_cinematics, main
from highlights.config import RecordingConfig
from highlights.models import CameraAction, Moment
from highlights.recording.sequences import build_actions_file
from highlights.recording.xray_cam import add_xray_sequences
from highlights.report import write_report

SHOOTER = "111"


def _pov(number, kills, start=6400, end=7360):
    sequence = _sequence(number, start, end)
    sequence.moments = [Moment(steamid=SHOOTER, name="a", side="T", round_index=0, kills=kills)]
    return sequence


def _kill(seconds, victim="222", start=6400, **flags):
    return make_kill(start + round(seconds * 64), attacker=SHOOTER, victim=victim, **flags)


def _demo(kills):
    return make_demo(kills, [make_round(0, 0, 40000)])


def _no_positions(demo, ticks):
    return {}


def test_auto_only_adds_a_take_for_plays_that_earn_one():
    wall = _pov(1, [_kill(8.0, penetrated=1)])
    plain = _pov(2, [_kill(8.0, start=20000)], start=20000, end=20960)
    smoke = _pov(
        3, [_kill(5.0, start=30000, thrusmoke=True), _kill(12.0, victim="333", start=30000)], start=30000, end=30960
    )
    demo = _demo([k for s in (wall, plain, smoke) for m in s.moments for k in m.kills])
    result, spatial = add_xray_sequences([wall, plain, smoke], {1: {"a": 1}, 2: {"a": 2}, 3: {"a": 3}}, demo, _no_positions)
    assert [s.number for s in result] == [1, 2, 3, 4, 5]  # wall + take, plain alone, smoke + take
    assert [s.cinematic_of for s in result] == [None, 1, None, None, 4]
    take = result[1]
    assert take.xray["windows"][0]["reasons"] == ["wallbang"]
    assert take.splice_segments == [(7.0, 8.5)] and take.campath is None and take.slowmo is None
    assert spatial[2] == {"a": 1} and spatial[5] == {"a": 3}  # takes share their POV's spatial data
    only_smoke = result[4].xray["windows"]
    assert len(only_smoke) == 1 and only_smoke[0]["reasons"] == ["through smoke"]  # the plain kill is left alone


def test_all_windows_every_selected_kill_and_off_changes_nothing():
    play = _pov(1, [_kill(5.0), _kill(12.0, victim="333")])
    demo = _demo(play.moments[0].kills)
    result, _ = add_xray_sequences([play], {}, demo, _no_positions, mode="all")
    assert len(result) == 2 and len(result[1].xray["windows"]) == 2
    unchanged, _ = add_xray_sequences([play], {}, demo, _no_positions, mode="off")
    assert unchanged == [play]
    none, _ = add_xray_sequences([play], {}, demo, _no_positions, mode="auto")
    assert none == [play]  # nothing qualifies: the play is not touched


def test_a_kill_through_a_player_is_not_a_wallbang():
    kill = _kill(8.0, penetrated=1)
    play = _pov(1, [kill])
    demo = _demo([kill])
    players = [(SHOOTER, (0.0, 0.0, 0.0), True), ("222", (1000.0, 0.0, 0.0), True), ("333", (500.0, 0.0, 0.0), True)]
    result, _ = add_xray_sequences([play], {}, demo, lambda d, ticks: {kill.tick - 1: players})
    assert result == [play]


def test_fade_length_reaches_the_windows():
    play = _pov(1, [_kill(8.0, penetrated=1)])
    result, _ = add_xray_sequences([play], {}, _demo(play.moments[0].kills), _no_positions, fade=0.8)
    window = result[1].xray["windows"][0]
    assert (window["fade_in"], window["fade_out"]) == (0.8, 0.8) and result[1].xray["fade_seconds"] == 0.8


# --- recording ----------------------------------------------------------------------


def _actions(sequences, tmp_path):
    demo = _demo([k for s in sequences for m in s.moments for k in m.kills])
    demo.slots = {SHOOTER: 1}
    actions = build_actions_file(tmp_path / "x.dem", sequences, demo, RecordingConfig(), tmp_path)
    return actions.write().read_text(encoding="utf-8")


def test_only_the_xray_take_turns_the_cvar_on(tmp_path):
    pov = _pov(1, [_kill(8.0, penetrated=1)])
    pov.cameras = [CameraAction(pov.start_tick, SHOOTER)]
    take = replace(pov, number=2, cinematic_of=1, xray={"windows": []})
    plain = _actions([pov], tmp_path)
    assert "spec_show_xray 0" in plain and "spec_show_xray 1" not in plain
    both = _actions([pov, take], tmp_path)
    assert both.count("spec_show_xray 0") == 1 and both.count("spec_show_xray 1") == 1


# --- splice step and report ---------------------------------------------------------


def test_xray_is_spliced_onto_the_pov_clip_first_with_its_fades(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        "highlights.video.splice.splice_clip",
        lambda ffmpeg, clip, take, segments, out, crf, tilt=False, fades=None: calls.append((take.name, fades)) or out,
    )
    pov = _pov(1, [_kill(8.0)])
    real = replace(pov, number=2, cinematic_of=1, campath=Path("a.dz"), splice_segments=[(2.0, 4.0)])
    xray = replace(
        pov, number=3, cinematic_of=1, splice_segments=[(7.0, 8.5)],
        xray={"windows": [{"start_seconds": 7.0, "end_seconds": 8.5, "fade_in": 0.4, "fade_out": 0.4}]},
    )
    rec = lambda s, n: (SimpleNamespace(sequence=s), Path(n))  # noqa: E731
    result = _splice_cinematics(Path("ffmpeg"), [rec(pov, "pov.mp4"), rec(real, "real.mp4"), rec(xray, "xray.mp4")], tmp_path, 18)
    assert [c[0] for c in calls] == ["xray.mp4", "real.mp4"]  # x-ray straight onto the POV clip, cam path over it
    assert calls[0][1] == [(7.0, 8.5, 0.4, 0.4)] and calls[1][1] is None
    assert [o.sequence.number for o, _ in result] == [1]  # the x-ray take never reaches the concat


def test_report_describes_the_xray_take(tmp_path):
    pov = _pov(1, [_kill(8.0, penetrated=1)])
    pov.cameras = [CameraAction(pov.start_tick, SHOOTER)]
    demo = _demo(pov.moments[0].kills)
    take = replace(pov, number=2, cinematic_of=1, xray={"mode": "auto", "windows": []})
    path = tmp_path / "r.json"
    write_report(path, demo, [pov, take], spatial_by_sequence={})
    sequences = json.loads(path.read_text())["sequences"]
    assert sequences[0]["xray"] is None and sequences[1]["xray"]["of_sequence"] == 1


def test_options_exist_on_run_and_batch_and_clash_with_suggest():
    for command in (["run", "--help"], ["batch", "--help"]):
        out = CliRunner().invoke(main, command).output
        assert "--xray [off|auto|all]" in out and "--xray-fade" in out
