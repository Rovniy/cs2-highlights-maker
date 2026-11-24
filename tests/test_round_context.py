from conftest import make_demo, make_kill, make_round

from highlights.analysis.scoring import context_tags, round_context, score_moment, wins_needed
from highlights.models import Moment


def _match(winners, equipment=None):
    rounds = [make_round(i, i * 10000, i * 10000 + 9000, winner=w) for i, w in enumerate(winners)]
    # Team A (a1, a2) is CT for rounds 0-11 and T afterwards.
    rosters = {
        i: {"a1": "CT" if i < 12 else "T", "a2": "CT" if i < 12 else "T", "b1": "T" if i < 12 else "CT"}
        for i in range(len(winners))
    }
    demo = make_demo([], rounds, rosters=rosters)
    demo.equipment = equipment or {}
    return demo


def test_wins_needed_follows_mr12_and_mr3_overtimes():
    assert [wins_needed(n) for n in (0, 23, 24, 29, 30, 36)] == [13, 13, 16, 16, 19, 22]


def test_match_point_is_found_across_the_side_switch():
    # Team A wins the first 12 as CT, so round 13 (index 12, A now T) is A's match point.
    demo = _match(["CT"] * 12 + ["CT"])
    context = round_context(demo)
    assert not context[11]["match_point"] and context[12]["match_point"]


def test_eco_and_anti_eco_tags_change_the_score():
    equipment = {0: {"a1": 800, "a2": 1000, "b1": 4500}}
    demo = _match(["CT"], equipment)
    context = round_context(demo)[0]
    eco = Moment("a1", "a1", "CT", 0, [make_kill(5000, attacker="a1"), make_kill(5100, attacker="a1")])
    rich = Moment("b1", "b1", "T", 0, [make_kill(5000, attacker="b1"), make_kill(5100, attacker="b1")])
    assert context_tags(eco, context) == ["eco"] and context_tags(rich, context) == ["anti-eco"]
    assert score_moment(eco, demo, context) > score_moment(eco, demo, None)
    assert score_moment(rich, demo, context) < score_moment(rich, demo, None)
