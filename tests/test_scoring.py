from conftest import make_demo, make_kill, make_round

from highlights.analysis.moments import cluster_moments
from highlights.analysis.scoring import score_moment, score_moments
from highlights.models import Clutch


def _moment(kills, demo):
    moments = cluster_moments(kills, demo.tickrate)
    assert len(moments) == 1
    return moments[0]


def test_ace_scores_higher_than_triple():
    rounds = [make_round(0, 0, 100000, winner="CT"), make_round(1, 100000, 200000)]
    ace_kills = [make_kill(1000 + i * 100, victim=str(i)) for i in range(5)]
    triple_kills = [make_kill(1000 + i * 100, victim=str(i)) for i in range(3)]
    demo = make_demo(ace_kills, rounds)

    ace = score_moment(_moment(ace_kills, demo), demo)
    triple = score_moment(_moment(triple_kills, demo), demo)
    assert ace > triple > 0


def test_knife_kill_bonus():
    rounds = [make_round(0, 0, 100000, winner="CT"), make_round(1, 100000, 200000)]
    plain = [make_kill(1000)]
    knife = [make_kill(1000, weapon="knife")]
    demo = make_demo(plain, rounds)
    assert score_moment(_moment(knife, demo), demo) > score_moment(_moment(plain, demo), demo)


def test_won_clutch_bonus_scales_with_opponents():
    rounds = [make_round(0, 0, 100000, winner="CT"), make_round(1, 100000, 200000)]
    kills = [make_kill(1000), make_kill(1200, victim="333")]
    demo = make_demo(kills, rounds)

    base = _moment(kills, demo)
    v3 = _moment(kills, demo)
    v3.clutch = Clutch("111", "p", "CT", 3, True, 2, 900, 5000, 0)
    v1 = _moment(kills, demo)
    v1.clutch = Clutch("111", "p", "CT", 1, True, 2, 900, 5000, 0)

    assert score_moment(v3, demo) > score_moment(v1, demo) > score_moment(base, demo)


def test_lost_round_penalty():
    rounds = [
        make_round(0, 0, 100000, winner="T"),
        make_round(1, 100000, 200000, winner="CT"),
    ]
    kills_lost = [make_kill(1000, attacker_side="CT"), make_kill(1100, attacker_side="CT", victim="333")]
    kills_won = [
        make_kill(101000, round_index=1, attacker_side="CT"),
        make_kill(101100, round_index=1, attacker_side="CT", victim="333"),
    ]
    demo = make_demo(kills_lost + kills_won, rounds)
    lost = score_moment(_moment(kills_lost, demo), demo)
    won = score_moment(_moment(kills_won, demo), demo)
    assert won > lost


def test_score_moments_fills_scores():
    rounds = [make_round(0, 0, 100000), make_round(1, 100000, 200000)]
    kills = [make_kill(1000), make_kill(1100, victim="333")]
    demo = make_demo(kills, rounds)
    moments = cluster_moments(kills, demo.tickrate)
    score_moments(moments, demo)
    assert all(m.score > 0 for m in moments)


def test_zeus_kill_bonus():
    rounds = [make_round(0, 0, 100000, winner="CT"), make_round(1, 100000, 200000)]
    plain = [make_kill(1000)]
    zeus = [make_kill(1000, weapon="taser")]
    demo = make_demo(plain, rounds)
    assert score_moment(_moment(zeus, demo), demo) > score_moment(_moment(plain, demo), demo)


def test_wallbang_bonus():
    rounds = [make_round(0, 0, 100000, winner="CT"), make_round(1, 100000, 200000)]
    plain = [make_kill(1000)]
    wallbang = [make_kill(1000, penetrated=1)]
    demo = make_demo(plain, rounds)
    assert score_moment(_moment(wallbang, demo), demo) > score_moment(_moment(plain, demo), demo)


def test_pistol_round_multiplier():
    rounds = [
        make_round(0, 0, 100000, winner="CT"),
        make_round(1, 100000, 200000, winner="CT"),
        make_round(2, 200000, 300000, winner="CT"),
    ]
    pistol = [make_kill(1000, round_index=0)]
    mid = [make_kill(101000, round_index=1)]
    demo = make_demo(pistol, rounds)
    assert score_moment(_moment(pistol, demo), demo) > score_moment(_moment(mid, demo), demo)
