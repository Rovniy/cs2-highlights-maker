import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner
from conftest import make_demo, make_kill, make_round
from test_dyn_sequences import pov
from test_static_cams import PLAYER, SPATIAL

from highlights.cli import _splice_cinematics, main
from highlights.floors import Floors
from highlights.recording.campaths import parse_campath
from highlights.recording.static_cams import StaticCam
from highlights.recording.static_downtime import add_static_downtime, choose, plan_passes

# pov(1): 15 s from tick 6400, one kill 8 s in -> down-time passes 0-7 s and 9-15 s.
# SPATIAL: the POV player (111, T) stands at the origin. Two CT enemies wait at y 1000.
ENEMY = StaticCam("enemy", (1000.0, 1000.0, 300.0), pitch=15.0, yaw=180.0)  # looks at y 1000, not at the origin
ROSTER = {0: {"111": "T", "222": "CT", "333": "CT"}}


def demo(rosters=ROSTER):
    d = make_demo([make_kill(6912, attacker="111", victim="222")], [make_round(0, 0, 100000)], rosters=rosters)
    d.player_names.update({"222": "Two", "333": "Three"})
    return d


def sampler(enemies=("222", "333"), calls=None):
    def sample(d, ticks):
        if calls is not None:
            calls.append(ticks)
        spots = [("111", (0.0, 0.0, 0.0), True)] + [(sid, (i * 100.0, 1000.0, 0.0), True) for i, sid in enumerate(enemies)]
        return {t: spots for t in ticks}

    return sample


def test_passes_are_the_down_time_minus_what_other_takes_cut_in():
    assert plan_passes(pov(1), [], 64.0) == [(0.0, 7.0), (9.0, 15.0)]
    real = replace(pov(1), number=2, cinematic_of=1, splice_segments=[(0.0, 7.0)])
    assert plan_passes(pov(1), [real], 64.0) == [(9.0, 15.0)]


def test_pov_mode_takes_the_cam_on_the_player_and_never_samples_enemies(tmp_path):
    calls = []
    sequences, spatial = add_static_downtime(
        [pov(1)], [ENEMY, PLAYER], {1: SPATIAL}, demo(), "pov", tmp_path, sampler(calls=calls)
    )
    assert calls == []
    take = sequences[1]
    assert take.cinematic_of == 1 and take.static_cam is None and take.dynamic_cam is None
    assert take.splice_segments == [(0.0, 7.0), (9.0, 15.0)]
    assert [c["shows"] for c in take.static_downtime] == ["pov", "pov"] and take.static_downtime[0]["slug"] == "player"
    path = parse_campath(take.campath)
    assert len(path.keyframes) == 4 and path.keyframes[0].pos == PLAYER.pos and spatial[2] is SPATIAL


def test_enemies_mode_takes_the_cam_on_the_other_team(tmp_path):
    calls = []
    sequences, _ = add_static_downtime(
        [pov(1)], [PLAYER, ENEMY], {1: SPATIAL}, demo(), "enemies", tmp_path, sampler(calls=calls)
    )
    assert len(calls) == 1 and all((t - 6400) % 32 == 0 for t in calls[0])  # one query, every 0.5 s
    cut = sequences[1].static_downtime[0]
    assert cut["cam"] == "enemy" and cut["shows"] == "enemies"
    assert cut["enemies_avg"] == 2.0 and cut["enemies_seen"] == ["Three", "Two"]


def test_auto_prefers_two_enemies_else_the_player(tmp_path):
    two, _ = add_static_downtime([pov(1)], [PLAYER, ENEMY], {1: SPATIAL}, demo(), "auto", tmp_path, sampler())
    assert two[1].static_downtime[0]["shows"] == "enemies"
    one, _ = add_static_downtime([pov(1)], [PLAYER, ENEMY], {1: SPATIAL}, demo(), "auto", tmp_path, sampler(("222",)))
    assert one[1].static_downtime[0]["shows"] == "pov" and one[1].static_downtime[0]["cam"] == "player"


def test_teammates_are_not_enemies_and_a_floor_hides_a_level_below(tmp_path):
    same_side = {0: {"111": "T", "222": "T", "333": "T"}}
    none, _ = add_static_downtime([pov(1)], [ENEMY], {1: SPATIAL}, demo(same_side), "enemies", tmp_path, sampler())
    assert len(none) == 1  # nobody to show: no take
    above = Floors.from_points([(x, y, 250.0) for x in range(-200, 1201, 50) for y in range(700, 1301, 50)])  # cam z 300
    hidden, _ = add_static_downtime([pov(1)], [ENEMY], {1: SPATIAL}, demo(), "enemies", tmp_path, sampler(), above)
    assert len(hidden) == 1


def test_choose_needs_enough_of_the_pass():
    looks = [{"cam": "a", "player_share": 0.4, "enemy_share": 0.4, "enemies_avg": 3.0}]
    assert choose(looks, "auto") is None
    looks.append({"cam": "b", "player_share": 0.9, "enemy_share": 0.6, "enemies_avg": 1.0})
    assert choose(looks, "auto")[0]["cam"] == "b" and choose(looks, "auto")[1] == "pov"
    assert choose(looks, "enemies") == (looks[1], "enemies")


def test_splice_order_is_cam_path_then_static_then_generated(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        "highlights.video.splice.splice_clip",
        lambda ffmpeg, clip, take, segments, out, crf, tilt=False: calls.append(take.name) or out,
    )
    base = pov(1)
    takes = [
        replace(base, number=2, cinematic_of=1, campath=Path("g"), splice_segments=[(0.0, 2.0)], dynamic_cam=[{}]),
        replace(base, number=3, cinematic_of=1, campath=Path("s"), splice_segments=[(9.0, 12.0)], static_downtime=[{}]),
        replace(base, number=4, cinematic_of=1, campath=Path("r"), splice_segments=[(3.0, 6.0)]),
    ]
    recorded = [(SimpleNamespace(sequence=s), Path(f"{s.number}.mp4")) for s in [base, *takes]]
    _splice_cinematics(Path("ffmpeg"), recorded, tmp_path, 18)
    assert calls == ["4.mp4", "3.mp4", "2.mp4"]


def test_report_lists_the_static_down_time(tmp_path):
    from highlights.report import write_report

    sequences, _ = add_static_downtime([pov(1)], [PLAYER], {1: SPATIAL}, demo(), "pov", tmp_path, sampler())
    report = tmp_path / "r.json"
    write_report(report, demo(), sequences, spatial_by_sequence={})
    cinematic = json.loads(report.read_text(encoding="utf-8"))["sequences"][1]["cinematic"]
    assert [c["start_seconds"] for c in cinematic["static_downtime"]] == [0.0, 9.0]
    assert cinematic["splice_segments"][1] == {"start_seconds": 9.0, "end_seconds": 15.0}


def test_cli_rejects_static_downtime_with_downtime_cam(tmp_path):
    dem = tmp_path / "a.dem"
    dem.write_bytes(b"")
    for command in (["run", str(dem)], ["batch", str(dem)]):
        result = CliRunner().invoke(main, [*command, "--static-downtime", "pov", "--downtime-cam", "heli"])
        assert result.exit_code != 0 and "pick one" in result.output
