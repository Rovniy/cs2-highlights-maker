import pytest
from conftest import make_demo, make_kill, make_round

from highlights.demo.players import PlayerNotFound, resolve_players

A = "76561198000000001"
B = "76561198000000002"
C = "76561198000000003"


def _demo():
    rounds = [make_round(0, 0, 100000, winner="CT")]
    kills = [
        make_kill(1000, attacker=A, victim=B),
        make_kill(2000, attacker=B, victim=C),
        make_kill(3000, attacker=C, victim=A),
    ]
    return make_demo(kills, rounds)


def test_resolve_players_by_steamid_list():
    demo = _demo()
    assert resolve_players(demo, [A, B]) == [A, B]


def test_resolve_players_by_name():
    demo = _demo()
    assert resolve_players(demo, ["Player76561198000000002"]) == [B]


def test_resolve_players_comma_separated_item():
    demo = _demo()
    assert resolve_players(demo, [f"{A}, {B}"]) == [A, B]


def test_resolve_players_mixed_names_and_steamids():
    demo = _demo()
    assert resolve_players(demo, ["player76561198000000001", C]) == [A, C]


def test_resolve_players_dedupes_preserving_order():
    demo = _demo()
    assert resolve_players(demo, [A, f"player{B}", B]) == [A, B]


def test_resolve_players_unknown_raises():
    demo = _demo()
    with pytest.raises(PlayerNotFound):
        resolve_players(demo, ["76561198000000099"])


def test_resolve_players_empty_raises():
    demo = _demo()
    with pytest.raises(PlayerNotFound, match="No players given"):
        resolve_players(demo, [])
    with pytest.raises(PlayerNotFound, match="No players given"):
        resolve_players(demo, ["", " , "])


def test_resolve_players_ambiguous_name_raises():
    demo = _demo()
    with pytest.raises(PlayerNotFound, match="matches several players"):
        resolve_players(demo, ["player"])