from conftest import make_demo, make_kill, make_round

from highlights.analysis.xray import Window, blockers_on_line, classify_kills, plan_windows, xray_reasons
from highlights.models import CameraAction, Moment, Sequence

SHOOTER, VICTIM, MATE = "111", "222", "333"


def _kill(**flags):
    return make_kill(5000, attacker=SHOOTER, victim=VICTIM, **flags)


def _players(bystander=None):
    players = [(SHOOTER, (0.0, 0.0, 0.0), True), (VICTIM, (1000.0, 0.0, 0.0), True)]
    if bystander is not None:
        players.append((MATE, bystander, True))
    return players


def test_wallbang_and_smoke_earn_an_xray_but_a_plain_kill_does_not():
    assert xray_reasons(_kill(penetrated=1)) == ("wallbang",)
    assert xray_reasons(_kill(thrusmoke=True)) == ("through smoke",)
    assert xray_reasons(_kill(penetrated=2, thrusmoke=True)) == ("wallbang", "through smoke")
    assert xray_reasons(_kill()) == ()


def test_a_player_on_the_line_is_not_a_wall():
    kill = _kill(penetrated=1)
    in_the_way = _players((500.0, 10.0, 0.0))
    assert blockers_on_line(kill, in_the_way) == 1
    assert xray_reasons(kill, 1) == ()  # through a player only: no x-ray
    assert xray_reasons(_kill(penetrated=2), 1) == ("wallbang",)  # a wall as well
    assert blockers_on_line(kill, _players((500.0, 300.0, 0.0))) == 0  # off to the side
    assert blockers_on_line(kill, _players((-300.0, 0.0, 0.0))) == 0  # behind the shooter
    dead = [(s, p, False if s == MATE else a) for s, p, a in _players((500.0, 10.0, 0.0))]
    assert blockers_on_line(kill, dead) == 0
    assert blockers_on_line(kill, [(SHOOTER, (0.0, 0.0, 0.0), True)]) == 0  # no victim position: unknown


def _sequence(kills):
    moment = Moment(steamid=SHOOTER, name="s", side="CT", round_index=0, kills=kills)
    return Sequence(1, 4000, 6000, [CameraAction(4000, SHOOTER)], "x", 1.0, 0, [moment])


def test_classify_uses_positions_when_it_has_them_and_the_raw_count_when_not():
    through_player = make_kill(5000, attacker=SHOOTER, victim=VICTIM, penetrated=1)
    wall = make_kill(5400, attacker=SHOOTER, victim="444", penetrated=1)
    smoke = make_kill(5600, attacker=SHOOTER, victim="555", thrusmoke=True)
    plain = make_kill(5800, attacker=SHOOTER, victim="666")
    sequence = _sequence([through_player, wall, smoke, plain])
    demo = make_demo([through_player, wall, smoke, plain], [make_round(0, 0, 40000)])

    def sample(_, ticks):
        assert ticks == [4999, 5399]  # only the penetrating kills are queried
        return {
            4999: _players((500.0, 0.0, 0.0)),
            5399: [(SHOOTER, (0.0, 0.0, 0.0), True), ("444", (1000.0, 0.0, 0.0), True)],
        }

    found = classify_kills([sequence], demo, sample)
    assert found == {(5400, SHOOTER, "444"): ("wallbang",), (5600, SHOOTER, "555"): ("through smoke",)}
    # No position data at all: trust `penetrated`.
    assert (5000, SHOOTER, VICTIM) in classify_kills([sequence], demo, lambda *_: {})


def test_windows_surround_the_kill_with_ramps_outside_the_hold():
    (w,) = plan_windows([(10.0, 5000, ("wallbang",))], duration=30.0)
    assert (w.start, w.end, w.fade_in, w.fade_out) == (9.0, 10.5, 0.4, 0.4)
    assert w.kills == (5000,) and w.reasons == ("wallbang",)


def test_ramps_shrink_at_the_clip_edges():
    (early,) = plan_windows([(0.5, 1, ())], duration=30.0)
    assert (early.start, early.fade_in) == (0.0, 0.0)  # x-ray from the first frame
    (late,) = plan_windows([(29.8, 2, ())], duration=30.0)
    assert (late.end, late.fade_out) == (30.0, 0.0)
    (near,) = plan_windows([(1.2, 3, ())], duration=30.0)
    assert (near.start, near.fade_in) == (0.2, 0.2)


def test_kills_whose_ramps_would_overlap_share_a_window():
    close = plan_windows([(10.0, 1, ("wallbang",)), (11.9, 2, ("through smoke",))], duration=30.0)
    assert len(close) == 1
    assert (close[0].start, close[0].end) == (9.0, 12.4)
    assert close[0].kills == (1, 2) and close[0].reasons == ("wallbang", "through smoke")
    apart = plan_windows([(10.0, 1, ()), (14.0, 2, ())], duration=30.0)
    assert len(apart) == 2 and apart[0].end < apart[1].start - 0.8  # room for both ramps
    assert plan_windows([], 30.0) == []
