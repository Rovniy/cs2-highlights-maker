import json
from pathlib import Path

import pytest
from conftest import make_demo, make_kill, make_round
from test_dyn_sequences import pov

from highlights.config import RecordingConfig
from highlights.recording.campaths import parse_campath
from highlights.recording.sequences import build_actions_file
from highlights.recording.static_cams import (
    StaticCam,
    add_static_sequences,
    assess,
    load_static_cams,
    parse_getpos,
    parse_mode,
)

# Player 111 stands at the origin for the whole 15 s clip; the victim dies at (0, 1000, 0).
SPATIAL = {
    "engagements": [
        {
            "is_selected_moment_kill": True,
            "victim_steamid": "222",
            "victim": {"xyz": {"x": 0.0, "y": 1000.0, "z": 0.0}},
            "engagement_center": {"x": 0.0, "y": 500.0, "z": 0.0},
        }
    ],
    "pov_trajectories": [
        {
            "steamid": "111",
            "samples": [{"seconds_from_clip_start": float(t), "xyz": {"x": 0.0, "y": 0.0, "z": 0.0}} for t in range(16)],
        }
    ],
}
BOTH = StaticCam("both", (0.0, -800.0, 300.0), pitch=10.0, yaw=90.0)  # behind the player, looking at the victim
PLAYER = StaticCam("player", (-800.0, 0.0, 300.0), pitch=20.0, yaw=0.0)  # side on, victim out of frame
AWAY = StaticCam("away", (0.0, -800.0, 300.0), pitch=10.0, yaw=-90.0)


def test_parse_getpos_output():
    assert parse_getpos("setpos -1234.5 567.8 128.0;setang 12.5 90.0 0.0") == ((-1234.5, 567.8, 128.0), 12.5, 90.0)
    assert parse_getpos("setpos 1 2 3; setang -5 -170 0") == ((1.0, 2.0, 3.0), -5.0, -170.0)
    assert parse_getpos("setpos_exact -1369.489502 724.275085 1696.280151;setang_exact 12.033976 43.693214 0.000000") == (
        (-1369.489502, 724.275085, 1696.280151), 12.033976, 43.693214
    )
    assert parse_getpos("garbage") is None


def test_parse_mode():
    assert parse_mode(None) is None and parse_mode("AUTO") == "auto" and parse_mode("all") == "all"
    assert parse_mode("a-site, b-long") == ["a-site", "b-long"]


def test_load_from_toml_and_single_point_campath(tmp_path):
    folder = tmp_path / "Mirage"
    (folder / "static").mkdir(parents=True)
    (folder / "static.toml").write_text(
        '[a-site]\ngetpos = "setpos 10 20 30;setang 15 90 0"\nfov = 80\nlabel = "A from balcony"\n'
        '[mid]\npos = [1, 2, 3]\nangles = [0, 180]\n'
        '[broken]\ngetpos = "nope"\n',
        encoding="utf-8",
    )
    (folder / "static" / "window.campath").write_text(
        '<campath><points><p t="3" x="5" y="6" z="7" fov="70" rx="0" ry="30" rz="45"/></points></campath>',
        encoding="utf-8",
    )
    cams = {c.name: c for c in load_static_cams(tmp_path, "de_mirage")}
    assert set(cams) == {"a-site", "mid", "window"}
    assert cams["a-site"].pos == (10.0, 20.0, 30.0) and cams["a-site"].fov == 80 and cams["a-site"].label == "A from balcony"
    assert (cams["mid"].pitch, cams["mid"].yaw, cams["mid"].fov) == (0.0, 180.0, 90.0)
    window = cams["window"]
    assert window.pos == (5.0, 6.0, 7.0) and window.fov == 70
    assert window.pitch == pytest.approx(30) and window.yaw == pytest.approx(45)
    assert load_static_cams(tmp_path, "de_nuke") == []


def test_assess_counts_deaths_and_player_time():
    play = pov(1)
    both = assess(BOTH, play, SPATIAL, 64.0, {"222": "victim"})
    assert both["deaths_seen"] == ["victim"] and both["player_seconds"] == pytest.approx(15.0, abs=0.2)
    player = assess(PLAYER, play, SPATIAL, 64.0, {})
    assert player["deaths_seen"] == [] and player["player_seconds"] > 10
    away = assess(AWAY, play, SPATIAL, 64.0, {})
    assert away["deaths_seen"] == [] and away["player_seconds"] == 0


def test_auto_takes_the_cam_that_sees_the_death_and_holds_its_pose(tmp_path):
    sequences, spatial = add_static_sequences([pov(1)], [AWAY, PLAYER, BOTH], {1: SPATIAL}, 64.0, "auto", tmp_path)
    assert [s.number for s in sequences] == [1, 2]
    take = sequences[1]
    assert take.cinematic_of == 1 and take.static_cam["name"] == "both" and take.static_cam["slug"] == "both"
    assert take.splice_segments is None and take.slowmo is None and spatial[2] is SPATIAL
    path = parse_campath(take.campath)
    assert len(path.keyframes) == 4 and all(k.pos == BOTH.pos for k in path.keyframes)  # HLAE cubic needs 4
    assert path.duration == pytest.approx(15.0)
    assert path.keyframes[0].forward == pytest.approx(BOTH.forward, abs=1e-4)


def test_all_takes_every_cam_that_sees_something_best_first(tmp_path):
    sequences, _ = add_static_sequences([pov(1), pov(2, 20000, 20960)], [AWAY, PLAYER, BOTH], {1: SPATIAL}, 64.0, "all", tmp_path)
    # play 2 has no spatial data: nothing qualifies, so it gets no take
    assert [(s.number, s.cinematic_of, (s.static_cam or {}).get("name")) for s in sequences] == [
        (1, None, None), (2, 1, "both"), (3, 1, "player"), (4, None, None),
    ]


def test_named_cams_are_used_even_when_they_see_nothing(tmp_path):
    sequences, _ = add_static_sequences([pov(1)], [AWAY, BOTH], {1: SPATIAL}, 64.0, ["Away", "missing"], tmp_path)
    assert [s.static_cam["name"] for s in sequences[1:]] == ["away"]


def test_static_take_loads_its_campath_and_blocks_the_kill_feed(tmp_path):
    demo = make_demo([make_kill(6900, attacker="111")], [make_round(0, 0, 100000)], slots={"111": 3})
    sequences, _ = add_static_sequences([pov(1)], [BOTH], {1: SPATIAL}, 64.0, "auto", tmp_path / "cams")
    actions = build_actions_file(tmp_path / "m.dem", sequences, demo, RecordingConfig(), tmp_path / "w")
    groups = json.loads(actions.write().read_text())
    cmds = [a["cmd"] for a in groups[1]["actions"]]
    assert any(c.startswith("mirv_campath load") and "static_both_6400.campath" in c for c in cmds)
    assert any("attackerMatch=!x0 block=1" in c for c in cmds)


def test_report_describes_the_static_take(tmp_path):
    from highlights.report import write_report

    demo = make_demo([make_kill(6900, attacker="111")], [make_round(0, 0, 100000)])
    sequences, _ = add_static_sequences([pov(1)], [BOTH], {1: SPATIAL}, 64.0, "auto", tmp_path)
    report = tmp_path / "r.json"
    write_report(report, demo, sequences, spatial_by_sequence={})
    take = json.loads(report.read_text(encoding="utf-8"))["sequences"][1]
    assert take["cinematic"]["static_cam"]["name"] == "both" and take["cinematic"]["of_sequence"] == 1


def test_cli_rejects_static_cam_with_suggest_cinematic(tmp_path):
    from click.testing import CliRunner

    from highlights.cli import main

    dem = tmp_path / "a.dem"
    dem.write_bytes(b"")
    result = CliRunner().invoke(main, ["run", str(dem), "--suggest-cinematic", "--static-cam", "auto"])
    assert result.exit_code != 0 and "--static-cam" in result.output
