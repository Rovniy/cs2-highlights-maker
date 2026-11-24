"""Tests for the pure cinematic suggestion module (generated paths/XML only)."""

from test_campaths import _sequence, _spatial
from test_splice import write_oriented

from highlights.models import CameraAction, Clutch, Kill, Moment
from highlights.places import PlaceMap
from highlights.recording.campaths import load_campaths
from highlights.recording.cinematic import add_cinematic_sequences
from highlights.recording.splice_plan import coverage
from highlights.recording.suggest import (
    ALTERNATIVES,
    GOOD_COVERAGE,
    MAX_ACTION_SPREAD,
    MAX_FLOOR_SPREAD,
    suggest_cinematics,
)

TICKRATE = 64.0
CENTER = (500.0, 0.0, 0.0)


def _kill(tick, weapon="ak47", **flags):
    return Kill(
        tick=tick,
        round_index=0,
        attacker_steamid="111",
        attacker_name="hero",
        attacker_side="CT",
        victim_steamid="222",
        victim_name="victim",
        weapon=weapon,
        **flags,
    )


def _moment(kills, score=7.0, clutch=None):
    return Moment(steamid="111", name="hero", side="CT", round_index=0, kills=kills, clutch=clutch, score=score)


def _play2(tmp_path, paths_spec, seconds=20, moments=(), route=None, centers=(CENTER,), score=None):
    """Like test_splice._play but with moments, a clip route and several kill centers."""
    for name, points in paths_spec.items():
        write_oriented(tmp_path / "Nuke", name, points)
    paths = load_campaths(tmp_path, "de_nuke")
    base = _sequence(1, 5000, 5000 + int(TICKRATE * seconds))
    changes = {"cameras": [CameraAction(5000, "111")], "moments": list(moments)}
    if score is not None:
        changes["score"] = score
    sequence = type(base)(**{**base.__dict__, **changes})
    samples = [
        {"seconds_from_clip_start": float(t), "xyz": {"x": 500.0, "y": 0.0, "z": 0.0}}
        for t in range(0, seconds + 1)
    ]
    engagements = [
        {"is_selected_moment_kill": True, "engagement_center": {"x": x, "y": y, "z": z}} for x, y, z in centers
    ]
    spatial = {
        1: {
            **_spatial(*CENTER),
            "engagements": engagements,
            "pov_trajectories": [{"steamid": "111", "samples": samples}],
        }
    }
    if route:
        spatial[1]["route"] = route
    return sequence, paths, spatial


def _three_kill_moment():
    return _moment([_kill(5000 + 640 * i) for i in range(3)], score=7.0)


def _stationary(x, duration=30):
    """Keyframes for a camera parked at (x, 0, 0) looking along +x."""
    return [(0, x, 0, 0, 0, 0), (duration, x, 0, 0, 0, 0)]


def _away(x, duration=30):
    """Keyframes for a camera parked at (x, 0, 0) looking away from +x."""
    return [(0, x, 0, 0, 0, 180), (duration, x, 0, 0, 0, 180)]


def test_strong_compact_clip_with_covering_path_is_good(tmp_path):
    """3 kills in one spot + a path that sees the player >= GOOD_COVERAGE -> good."""
    sequence, paths, spatial = _play2(
        tmp_path,
        {
            "good": _stationary(100),  # 400u from the player, sees most of the clip
            "wide": _stationary(-3000),  # far away, sees the whole clip
        },
        moments=[_three_kill_moment()],
    )
    suggestion = suggest_cinematics([sequence], paths, spatial, TICKRATE)[1]
    assert suggestion["verdict"] == "good"
    assert suggestion["best"]["name"] == "good"  # closest eligible path, not the farther one
    assert suggestion["best"]["coverage_seconds"] >= GOOD_COVERAGE
    assert "3K" in suggestion["why"]
    assert [c["name"] for c in suggestion["alternatives"]] == ["wide"]


def test_spread_out_kills_are_not_good_but_still_possible(tmp_path):
    """Engagement centers 3000 units apart: why mentions the span, verdict possible."""
    assert MAX_ACTION_SPREAD < 3000.0
    sequence, paths, spatial = _play2(
        tmp_path,
        {"good": _stationary(100)},
        moments=[_three_kill_moment()],
        centers=[(0.0, 0.0, 0.0), (3000.0, 0.0, 0.0)],
    )
    suggestion = suggest_cinematics([sequence], paths, spatial, TICKRATE)[1]
    assert suggestion["verdict"] == "possible"
    assert suggestion["best"] is not None
    spans = [float(w.split()[2]) for w in suggestion["why"] if "kills span" in w]
    assert len(spans) == 1 and abs(spans[0] - 3000.0) <= 1.0


def test_kills_on_different_floors_mention_floors(tmp_path):
    """Selected kills more than MAX_FLOOR_SPREAD apart in z -> why mentions floors."""
    assert MAX_FLOOR_SPREAD < 500.0
    sequence, paths, spatial = _play2(
        tmp_path,
        {"good": _stationary(100)},
        moments=[_three_kill_moment()],
        centers=[(0.0, 0.0, 0.0), (0.0, 0.0, 500.0)],
    )
    suggestion = suggest_cinematics([sequence], paths, spatial, TICKRATE)[1]
    assert any("floor" in reason for reason in suggestion["why"])
    assert suggestion["verdict"] == "possible"


def test_no_path_covering_enough_has_no_best(tmp_path):
    """A path that sees nobody: best is None, verdict none, why mentions down time."""
    sequence, paths, spatial = _play2(
        tmp_path,
        {"blind": [(0, 400, 0, 0, 0, 180), (30, 400, 0, 0, 0, 180)]},  # faces away from the player
        moments=[_three_kill_moment()],
    )
    suggestion = suggest_cinematics([sequence], paths, spatial, TICKRATE)[1]
    assert suggestion["best"] is None
    assert suggestion["verdict"] == "none"
    assert any("down time" in reason for reason in suggestion["why"])


def test_weak_clip_with_a_good_path_is_possible_never_good(tmp_path):
    """One ordinary kill and a low score: possible while compact, never good."""
    sequence, paths, spatial = _play2(
        tmp_path,
        {"good": _stationary(100)},
        moments=[_moment([_kill(5300)], score=1.0)],
    )
    suggestion = suggest_cinematics([sequence], paths, spatial, TICKRATE)[1]
    assert suggestion["verdict"] == "possible"
    assert suggestion["best"] is not None


def test_worthiness_reasons_cover_clutch_special_and_score(tmp_path):
    """A won clutch, a special kill and a high score each make a clip strong."""
    clutch = Clutch("111", "hero", "CT", opponents=2, won=True, kills=1, start_tick=5300, end_tick=6000, round_index=0)
    cases = {
        "clutch": (_moment([_kill(5300)], score=1.0, clutch=clutch), None, "1v2 clutch"),
        "special": (_moment([_kill(5300, weapon="knife")], score=1.0), None, "special kill"),
        "score": (_moment([_kill(5300)], score=1.0), 7.5, "score 7.5"),
    }
    for kind, (moment, clip_score, reason) in cases.items():
        sequence, paths, spatial = _play2(
            tmp_path / kind, {"good": _stationary(100)}, moments=[moment], score=clip_score
        )
        suggestion = suggest_cinematics([sequence], paths, spatial, TICKRATE)[1]
        assert reason in suggestion["why"], kind
        assert suggestion["verdict"] == "good", kind


def test_route_overlap_counts_shared_places(tmp_path):
    """A clip route sharing one of two places with the path's route overlaps 0.5."""
    place_map = PlaceMap("de_nuke")
    for x in range(-150, 651, 50):  # label a wide stretch around the camera start at (0, 0, 0)
        place_map.add(float(x), 0.0, 0.0, "Mini")
    for x in range(850, 1151, 50):  # and around its far end at (1000, 0, 0)
        place_map.add(float(x), 0.0, 0.0, "BombsiteA")
    sequence, paths, spatial = _play2(
        tmp_path,
        {"good": [(0, 0, 0, 0, 0, 0), (30, 1000, 0, 0, 0, 0)]},  # camera walks +x through both places
        moments=[_three_kill_moment()],
        route={"text": "Mini > BombsiteA", "places": ["Mini", "BombsiteA"]},
    )
    suggestion = suggest_cinematics([sequence], paths, spatial, TICKRATE, place_map=place_map)[1]
    assert suggestion["clip_route"] == "Mini > BombsiteA"
    assert suggestion["best"]["route"] == ["Mini", "BombsiteA"]
    assert suggestion["best"]["route_overlap"] == 1.0  # the path passes through both clip places
    assert "path passes BombsiteA, Mini" in suggestion["why"]


def test_route_overlap_is_one_over_two_when_the_path_shares_one_place(tmp_path):
    """Clip route ["Mini", "BombsiteA"], path route ["Mini"] -> overlap 0.5."""
    place_map = PlaceMap("de_nuke")
    for x in range(-150, 651, 50):  # the whole path sits inside Mini
        place_map.add(float(x), 0.0, 0.0, "Mini")
    sequence, paths, spatial = _play2(
        tmp_path,
        {"good": [(0, 0, 0, 0, 0, 0), (30, 1000, 0, 0, 0, 0)]},  # camera walks +x, never reaches BombsiteA
        moments=[_three_kill_moment()],
        route={"text": "Mini > BombsiteA", "places": ["Mini", "BombsiteA"]},
    )
    suggestion = suggest_cinematics([sequence], paths, spatial, TICKRATE, place_map=place_map)[1]
    assert suggestion["best"]["route"] == ["Mini"]
    assert suggestion["best"]["route_overlap"] == 0.5  # one of the clip's two places
    assert "path passes Mini" in suggestion["why"]


def test_alternatives_are_other_candidates_in_order(tmp_path):
    """Up to ALTERNATIVES other candidates, same keys, in candidate order, excluding best."""
    sequence, paths, spatial = _play2(
        tmp_path,
        {
            "a": _stationary(-3000),  # far away, sees the whole clip
            "b": _stationary(-2000),
            "c": _stationary(-1000),
            "d": _away(430),  # nearest the kills, but faces away so it can never cut in
        },
        moments=[_three_kill_moment()],
        centers=[CENTER] * 5 + [(3000.0, 0.0, 0.0)] * 4,  # "a" passes closest overall, "c" is the closest that covers
    )
    suggestion = suggest_cinematics([sequence], paths, spatial, TICKRATE)[1]
    assert suggestion["best"]["name"] == "c"  # closest path that can actually cut in
    names = [c["name"] for c in suggestion["alternatives"]]
    assert names == ["d", "b"]  # candidate order (d ranks first: nearest), best excluded, capped
    assert len(names) <= ALTERNATIVES
    keys = {"name", "label", "distance", "coverage_seconds", "route", "route_overlap"}
    assert set(suggestion["best"]) == keys
    for candidate in suggestion["alternatives"]:
        assert set(candidate) == keys
        assert candidate["coverage_seconds"] is not None


def test_sequence_without_spatial_data_gets_no_position_data(tmp_path):
    """No spatial metadata -> verdict none and why ["no position data"]."""
    sequence, paths, _ = _play2(tmp_path, {"good": _stationary(100)})
    result = suggest_cinematics([sequence], paths, {}, TICKRATE)
    assert result[1] == {
        "verdict": "none",
        "why": ["no position data"],
        "clip_route": "",
        "best": None,
        "alternatives": [],
    }


def test_quiet_suppresses_output_and_default_still_logs(tmp_path, capsys):
    """quiet=True prints nothing; the default call still logs the candidates."""
    sequence, paths, spatial = _play2(tmp_path, {"good": _stationary(100)})
    add_cinematic_sequences([sequence], paths, spatial, TICKRATE, takes=1, splice=True, quiet=True)
    assert capsys.readouterr().out == ""
    add_cinematic_sequences([sequence], paths, spatial, TICKRATE, takes=1, splice=True)
    logged = capsys.readouterr().out
    assert "good" in logged and "candidates" in logged
    # The no-spatial-data warning is silenced too.
    add_cinematic_sequences([sequence], paths, {}, TICKRATE, splice=True, quiet=True)
    assert capsys.readouterr().out == ""


def test_results_keyed_by_input_numbers_and_inputs_unchanged(tmp_path):
    """Keys are the input sequence numbers; sequences come back untouched."""
    sequence, paths, spatial = _play2(tmp_path, {"good": _stationary(100)})
    second = _sequence(7, 20000, 20500)  # no spatial data for this one
    sequences = [sequence, second]
    before = [(s.number, s.start_tick, s.end_tick, s.campath, s.cinematic_of, s.splice_segments) for s in sequences]
    result = suggest_cinematics(sequences, paths, spatial, TICKRATE)
    assert set(result) == {1, 7}
    assert result[1]["best"] is not None
    assert result[7]["why"] == ["no position data"]
    after = [(s.number, s.start_tick, s.end_tick, s.campath, s.cinematic_of, s.splice_segments) for s in sequences]
    assert before == after


def test_suggestion_matches_what_the_splice_planner_actually_covers(tmp_path):
    """The coverage a suggestion reports is the plan the splice mode really made."""
    sequence, paths, spatial = _play2(tmp_path, {"good": _stationary(100)}, moments=[_three_kill_moment()])
    suggestion = suggest_cinematics([sequence], paths, spatial, TICKRATE)[1]
    result, _ = add_cinematic_sequences([sequence], paths, spatial, TICKRATE, takes=1, splice=True)
    cinematic = next(s for s in result if s.campath)
    assert suggestion["best"]["coverage_seconds"] == round(coverage(cinematic.splice_segments), 1)


def test_later_clips_keep_their_own_data_after_an_earlier_clip_gets_a_cinematic(tmp_path):
    """The result is renumbered; verdicts must still follow the INPUT numbers and their own spatial data."""
    from dataclasses import replace

    first, paths, spatial = _play2(
        tmp_path, {"good": _stationary(100)}, moments=[_three_kill_moment()]
    )
    second = replace(first, number=2)
    spatial[2] = {
        **spatial[1],
        "engagements": [
            {"is_selected_moment_kill": True, "engagement_center": {"x": x, "y": 0.0, "z": 0.0}}
            for x in (0.0, 3000.0)
        ],
    }
    result = suggest_cinematics([first, second], paths, spatial, TICKRATE)
    assert set(result) == {1, 2}
    assert result[1]["verdict"] == "good"
    assert result[2]["verdict"] == "possible"
    assert any("kills span" in why for why in result[2]["why"])
