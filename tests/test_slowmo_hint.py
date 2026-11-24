import pytest
from conftest import make_demo, make_kill, make_round

from highlights.models import Moment


def test_slowmo_hint_points_at_the_kill_in_clip_seconds():
    from highlights.analysis.selection import build_sequences
    from highlights.analysis.slowmo import best_kill
    from highlights.batch import slowmo_hint
    from highlights.config import RecordingConfig, SelectionConfig

    kills = [make_kill(5000 + i * 100) for i in range(3)] + [make_kill(5400, weapon="knife")]
    demo = make_demo(kills, [make_round(0, 0, 20000)])
    moment = Moment("111", "player111", "CT", 0, kills)
    [pov] = build_sequences([moment], demo, SelectionConfig(), RecordingConfig(), merge=False)
    hint = slowmo_hint(best_kill(moment), pov, demo.tickrate)
    assert hint["kill_tick"] == 5400 and hint["applied"] is False
    assert hint["clip_time_seconds"] == pytest.approx((5400 - pov.start_tick) / demo.tickrate, abs=1e-3)
    assert hint["start_seconds"] < hint["clip_time_seconds"] < hint["end_seconds"]
    assert slowmo_hint(None, pov, demo.tickrate) is None
