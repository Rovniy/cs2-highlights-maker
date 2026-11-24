from rich.console import Console

from highlights import report
from highlights.models import CameraAction, Sequence
from highlights.report import print_sequences_table
from highlights.routes import clip_route
from highlights.spatial import build_spatial_metadata

TICKRATE = 64.0


def _sequence(number=1, start=0, end=6400, cameras=None):
    return Sequence(
        number=number,
        start_tick=start,
        end_tick=end,
        cameras=cameras if cameras is not None else [CameraAction(tick=start, steamid="111")],
        label="test",
        score=1.0,
        round_index=0,
    )


def _track(steamid, samples):
    return {"steamid": steamid, "player": f"player{steamid}", "samples": samples}


def _sample(tick, place, start=0):
    return {
        "tick": tick,
        "seconds_from_clip_start": round((tick - start) / TICKRATE, 6),
        "xyz": {"x": 0.0, "y": 0.0, "z": 0.0},
        "place_name_hint": place,
    }


def _engagement(tick, selected=True, attacker=None, victim=None, start=0):
    return {
        "tick": tick,
        "seconds_from_clip_start": round((tick - start) / TICKRATE, 6),
        "attacker_steamid": "111",
        "victim_steamid": "222",
        "attacker": attacker,
        "victim": victim,
        "engagement_center": None,
        "place_name_hint": None,
        "is_pov_kill": True,
        "is_selected_moment_kill": selected,
    }


def test_route_follows_trajectory_and_compresses_duplicates():
    spatial = {
        "pov_trajectories": [
            _track("111", [
                _sample(0, "Catwalk"),
                _sample(640, "Catwalk"),
                _sample(1280, "ASite"),
                _sample(1920, "ASite"),
            ])
        ],
        "engagements": [],
    }
    route = clip_route(_sequence(), spatial)
    assert route["places"] == ["Catwalk", "ASite"]
    assert route["text"] == "Catwalk \u2192 ASite"


def test_only_active_camera_track_counts():
    cameras = [CameraAction(tick=0, steamid="111"), CameraAction(tick=1280, steamid="222")]
    spatial = {
        "pov_trajectories": [
            _track("111", [_sample(0, "Spawn"), _sample(640, "Catwalk"), _sample(1280, "Old")]),
            _track("222", [_sample(0, "Ignored"), _sample(1280, "BShort"), _sample(1920, "BSite")]),
        ],
        "engagements": [],
    }
    route = clip_route(_sequence(cameras=cameras), spatial)
    # The 1280 sample of the now-inactive player 111 is dropped, as is player
    # 222's sample from before the camera switched to them.
    assert route["places"] == ["Spawn", "Catwalk", "BShort", "BSite"]


def test_kills_only_selected_moment_kills_sorted_with_places():
    spatial = {
        "pov_trajectories": [],
        "engagements": [
            _engagement(2560, attacker={"place_name_hint": "Mid"}, victim={"place_name_hint": "CTSpawn"}),
            _engagement(1280, selected=False, attacker={"place_name_hint": "Nope"}),
            _engagement(640, attacker={"place_name_hint": "Catwalk"}, victim=None),
        ],
    }
    route = clip_route(_sequence(), spatial)
    assert [k["tick"] for k in route["kills"]] == [640, 2560]
    first, second = route["kills"]
    assert first["attacker_place"] == "Catwalk"
    assert first["victim_place"] is None
    assert first["seconds_from_clip_start"] == round(640 / TICKRATE, 6)
    assert second["attacker_place"] == "Mid"
    assert second["victim_place"] == "CTSpawn"


def test_empty_or_unavailable_spatial_returns_empty_route():
    empty = {"places": [], "text": "", "kills": []}
    assert clip_route(_sequence(), {}) == empty
    assert clip_route(_sequence(), {"status": "unavailable", "error": "boom"}) == empty
    assert clip_route(_sequence(), {"pov_trajectories": [], "engagements": []}) == empty


def _demo():
    from conftest import make_demo, make_round

    return make_demo([], [make_round(0, 0, 100000)])


def _sequence_with_route():
    cameras = [CameraAction(tick=0, steamid="111")]
    sequence = _sequence(cameras=cameras)
    spatial = {
        sequence.number: {
            "status": "ok",
            "route": {"places": ["Catwalk", "ASite"], "text": "Catwalk \u2192 ASite", "kills": []},
        }
    }
    return sequence, spatial


def test_table_shows_where_column_with_route(monkeypatch):
    sequence, spatial = _sequence_with_route()
    recording = Console(record=True, width=200)
    monkeypatch.setattr(report, "console", recording)
    print_sequences_table(_demo(), [sequence], spatial)
    output = recording.export_text()
    assert "Where" in output
    assert "Catwalk \u2192 ASite" in output


def test_table_has_no_where_column_without_spatial(monkeypatch):
    sequence, _ = _sequence_with_route()
    recording = Console(record=True, width=200)
    monkeypatch.setattr(report, "console", recording)
    print_sequences_table(_demo(), [sequence])
    assert "Where" not in recording.export_text()

    # Also unchanged when spatial is given but no sequence has a route text.
    recording = Console(record=True, width=200)
    monkeypatch.setattr(report, "console", recording)
    print_sequences_table(_demo(), [sequence], {sequence.number: {"route": {"text": ""}}})
    assert "Where" not in recording.export_text()


def test_build_spatial_metadata_includes_route(monkeypatch):
    # Synthesize positions so no real demo file is opened.
    positions = {
        tick: {
            "111": {
                "xyz": {"x": 0.0, "y": 0.0, "z": 0.0},
                "yaw": None,
                "pitch": None,
                "place_name_hint": place,
                "is_alive": True,
            }
        }
        for tick, place in [(0, "Catwalk"), (1600, "Catwalk"), (3200, "ASite")]
    }
    monkeypatch.setattr("highlights.spatial._query_positions", lambda path, ticks: (positions, ["X", "Y", "Z"], None))
    sequence = _sequence(start=0, end=3200)
    spatial = build_spatial_metadata(_demo(), [sequence])
    route = spatial[sequence.number]["route"]
    assert route["places"] == ["Catwalk", "ASite"]
    assert route["text"] == "Catwalk \u2192 ASite"
    assert route["kills"] == []
