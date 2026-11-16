from conftest import make_demo, make_kill, make_round

from highlights.analysis.killfilter import KillFilter, apply_kill_filter, parse_weapons, weapons_seen
from highlights.analysis.moments import cluster_moments
from highlights.analysis.scoring import score_moments


def _setup():
    rounds = [make_round(0, 0, 100000)]
    kills = [
        make_kill(2000, victim="v1", weapon="deagle", headshot=True),
        make_kill(2500, victim="v2", weapon="ak47", headshot=True),
        make_kill(3000, victim="v3", weapon="deagle"),
        make_kill(50000, attacker="333", victim="v1", weapon="awp"),
    ]
    demo = make_demo(kills, rounds)
    moments = cluster_moments(kills, demo.tickrate)
    score_moments(moments, demo)
    return demo, moments


def test_parse_weapons_aliases_prefix_and_commas():
    assert parse_weapons(["Deag", "awp,weapon_ssg08"]) == {"deagle", "awp", "ssg08"}
    assert parse_weapons(["m4"]) == {"m4a1", "m4a1_silencer"}
    assert parse_weapons([" , "]) == set()


def test_inactive_filter_returns_moments_unchanged():
    demo, moments = _setup()
    assert apply_kill_filter(moments, demo, KillFilter()) is moments


def test_weapon_filter_keeps_only_matching_kills_and_rescores():
    demo, moments = _setup()
    result = apply_kill_filter(moments, demo, KillFilter({"deagle"}))
    assert len(result) == 1
    assert [k.tick for k in result[0].kills] == [2000, 3000]
    assert result[0].describe() == "2K (deagle)"
    assert result[0].score < moments[0].score


def test_headshot_only_and_per_kill():
    demo, moments = _setup()
    result = apply_kill_filter(moments, demo, KillFilter(headshot_only=True, per_kill=True))
    assert [m.kills[0].tick for m in result] == [2000, 2500]
    assert all(m.kill_count == 1 and m.clutch is None and m.score > 0 for m in result)


def test_per_kill_without_weapon_splits_every_kill():
    demo, moments = _setup()
    result = apply_kill_filter(moments, demo, KillFilter(per_kill=True))
    assert len(result) == 4 and [m.first_tick for m in result] == sorted(m.first_tick for m in result)


def test_knife_matches_every_knife_skin():
    kill_filter = KillFilter({"knife"})
    assert kill_filter.matches(make_kill(1, weapon="knife_karambit"))
    assert not kill_filter.matches(make_kill(1, weapon="awp"))


def test_weapons_seen_most_used_first():
    demo, _ = _setup()
    assert weapons_seen([demo]) == ["deagle", "ak47", "awp"]
