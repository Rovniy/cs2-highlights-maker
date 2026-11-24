from conftest import make_demo, make_round
from test_splice import write_oriented


def test_buildup_ends_right_before_the_play_and_respects_the_mode(tmp_path):
    from highlights.broll import plan_buildups
    from highlights.recording.campaths import load_campaths

    write_oriented(tmp_path / "Nuke", "facing", [(0, 0, 0, 0, 0, 0), (8, 0, 0, 0, 0, 0)])
    paths = load_campaths(tmp_path, "de_nuke")
    demo = make_demo([], [make_round(0, 0, 100000)])
    demo.slots = {"111": 1}

    def sampler(d, ticks):
        return {t: [("111", (500.0, 0.0, 0.0), True)] for t in ticks}

    shots = plan_buildups(demo, paths, "players", {"clip": (0, 20000)}, 6.0, seed=1, sampler=sampler)
    shot = shots["clip"]
    assert shot.end_tick <= 20000 and 20000 - shot.end_tick < 0.5 * demo.tickrate
    assert shot.seconds == 6.0 and shot.visible == ["111"]
    assert plan_buildups(demo, paths, "empty", {"clip": (0, 20000)}, 6.0, sampler=sampler) == {}
