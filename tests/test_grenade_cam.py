from dataclasses import replace

from conftest import make_demo, make_kill, make_round

from highlights.analysis.selection import build_sequences
from highlights.config import Config
from highlights.demo.parser import Blind, Detonation, NadeHit
from highlights.models import CameraAction, Moment
from highlights.recording.grenade_cam import (
    HOLD,
    add_grenade_sequences,
    detonate,
    follow_keyframes,
    grenade_worth,
    plan_clip,
)

THROWER, ENEMY, MATE = "111", "222", "333"


def _flight(kind="he", start=5000, end=5400, entity=7, thrower=THROWER):
    """A grenade flying straight down +x at 10 units per tick (the entity outlives the pop)."""
    points = [(t, (float((t - start) * 10), 0.0, 100.0)) for t in range(start, end + 1)]
    return {"kind": kind, "thrower": thrower, "entity": entity, "start_tick": start, "end_tick": end, "points": points}


def _demo(kills=(), **events):
    rosters = {0: {THROWER: "CT", MATE: "CT", ENEMY: "T"}}
    demo = make_demo(list(kills), [make_round(0, 0, 40000)], rosters=rosters)
    demo.detonations = events.get("detonations", [])
    demo.blinds = events.get("blinds", [])
    demo.nade_hits = events.get("hits", [])
    return demo


def test_detonate_cuts_the_flight_where_the_grenade_went_off():
    demo = _demo(detonations=[
        Detonation(5100, "he", THROWER, 99, (0, 0, 0)),  # another grenade's entity
        Detonation(5100, "he", THROWER, 7, (1000.0, 0.0, 100.0)),
    ])
    flight = detonate(_flight(), demo)
    assert flight["end_tick"] == 5100 and flight["points"][-1][0] == 5100 and flight["burst"] == (1000.0, 0.0, 100.0)
    assert detonate(_flight(), _demo()) is None  # no detonation: a dud
    # Fire has its own entity id: matched by thrower and time, a little after the last sample.
    fire = _demo(detonations=[Detonation(5410, "fire", THROWER, 555, (1.0, 2.0, 3.0))])
    assert detonate(_flight("molotov"), fire)["end_tick"] == 5410
    assert detonate(_flight("smoke"), fire) is None


def test_grenade_worth_needs_a_kill_hits_or_a_flash_assist():
    he = detonate(_flight(), _demo(detonations=[Detonation(5100, "he", THROWER, 7, (0, 0, 0))]))
    kill = make_kill(5101, attacker=THROWER, victim=ENEMY, weapon="hegrenade")
    assert grenade_worth(he, _demo([kill])).reason == "HE kill"
    hits = [NadeHit(5101, THROWER, ENEMY, "hegrenade", 40), NadeHit(5101, THROWER, "444", "hegrenade", 30)]
    demo = _demo(hits=hits)
    demo.rosters[0]["444"] = "T"
    assert grenade_worth(he, demo).reason == "HE hits 2"
    team_hit = _demo(hits=[NadeHit(5101, THROWER, ENEMY, "hegrenade", 40), NadeHit(5101, THROWER, MATE, "hegrenade", 30)])
    assert grenade_worth(he, team_hit) is None  # a teammate does not count

    flash = detonate(_flight("flash"), _demo(detonations=[Detonation(5100, "flash", THROWER, 7, (0, 0, 0))]))
    blind = Blind(5100, THROWER, ENEMY, 2.5, 7)
    later_kill = make_kill(5100 + 64 * 2, attacker=MATE, victim=ENEMY)
    assert grenade_worth(flash, _demo([later_kill], blinds=[blind])).reason == "flash assist"
    too_late = make_kill(5100 + 64 * 5, attacker=MATE, victim=ENEMY)
    assert grenade_worth(flash, _demo([too_late], blinds=[blind])) is None
    assert grenade_worth(flash, _demo([later_kill], blinds=[Blind(5100, THROWER, ENEMY, 2.5, 8)])) is None


def test_follow_camera_rides_behind_and_holds_on_the_burst():
    flight = detonate(_flight(), _demo(detonations=[Detonation(5100, "he", THROWER, 7, (1000.0, 0.0, 100.0))]))
    keys = follow_keyframes(flight, clip_start_tick=4936, tickrate=64.0)
    assert keys[0][0] == 0.0 and keys[1][0] == 1.0  # held from the clip start, flight from 1 s
    t, pose = keys[5]
    grenade_x = (t - 1.0) * 64 * 10
    assert pose.pos[0] < grenade_x and pose.pos[2] > 100.0  # behind and above
    assert abs(pose.yaw) < 1.0 or abs(pose.yaw - 360) < 1.0  # looking down +x
    end_t, last = keys[-1]
    assert end_t == round((5100 - 4936) / 64 + HOLD["he"], 4) and last.pos == keys[-2][1].pos


def _clip(demo):
    moment = Moment(THROWER, "player111", "CT", 0, [make_kill(5200, attacker=THROWER, victim=ENEMY)])
    config = Config()
    [pov] = build_sequences([moment], demo, config.selection, config.recording, merge=False)
    return pov


def test_plan_clip_follows_only_the_pov_players_grenades_inside_the_clip():
    events = dict(detonations=[Detonation(5100, "he", THROWER, 7, (0, 0, 0)), Detonation(5100, "he", MATE, 8, (0, 0, 0))])
    demo = _demo(**events)
    pov = _clip(demo)
    mine, theirs = _flight(), _flight(entity=8, thrower=MATE)
    assert plan_clip(pov, [mine, theirs], demo, "auto") == []  # nothing significant
    [pick] = plan_clip(pov, [mine, theirs], demo, "all")
    assert pick.flight["thrower"] == THROWER and pick.reason == "he"
    switched = replace(pov, cameras=[CameraAction(pov.start_tick, MATE)])
    assert [p.flight["thrower"] for p in plan_clip(switched, [mine, theirs], demo, "all")] == [MATE]
    outside = _flight(start=pov.end_tick - 10, end=pov.end_tick + 300)
    assert plan_clip(pov, [outside], demo, "all") == []


def test_grenade_take_follows_its_pov_and_is_spliced_on_top(tmp_path):
    kill = make_kill(5101, attacker=THROWER, victim=ENEMY, weapon="hegrenade")
    demo = _demo([kill], detonations=[Detonation(5100, "he", THROWER, 7, (1000.0, 0.0, 100.0))])
    pov = _clip(demo)
    result, spatial = add_grenade_sequences([pov], {1: {"x": 1}}, demo, lambda d, wanted: [_flight()], tmp_path)
    assert [s.number for s in result] == [1, 2] and result[1].cinematic_of == 1
    take = result[1]
    assert take.dynamic_cam[0]["kind"] == "grenade" and take.dynamic_cam[0]["reason"] == "HE kill"
    assert take.campath.is_file() and take.splice_segments[0][0] < take.splice_segments[0][1]
    assert spatial[2] == {"x": 1}
    assert add_grenade_sequences([pov], {}, demo, lambda d, w: [], tmp_path) == ([pov], {})
