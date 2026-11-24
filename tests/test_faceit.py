import json
from pathlib import Path

import requests
from conftest import make_demo, make_kill, make_round

from highlights import faceit
from highlights.config import Config

MATCH_ID = "1-0a1b2c3d-0000-4000-8000-123456789abc"


def fake_match(status="FINISHED"):
    return {
        "match_id": MATCH_ID,
        "competition_name": "S59 NA Open1-8 East - Regular Season",
        "organizer_id": "org",
        "faceit_url": "https://www.faceit.com/{lang}/cs2/room/" + MATCH_ID,
        "best_of": 1,
        "status": status,
        "results": {"winner": "faction2", "score": {"faction1": 9, "faction2": 13}},
        "teams": {
            "faction1": {"name": "Alpha", "roster": [{"nickname": "a1", "game_player_id": "11"}]},
            "faction2": {"name": "Bravo", "roster": [{"nickname": "b1", "game_player_id": "21"}]},
        },
    }


def demo_with_standin():
    kills = [make_kill(100, attacker="11", victim="21")]
    demo = make_demo(kills, [make_round(0, 0, 1000)], rosters={0: {"11": "CT", "12": "CT", "21": "T"}})
    demo.player_names["12"] = "standin"
    demo.path = Path(f"{MATCH_ID}-1-1.dem")
    return demo


def test_match_id_from_file_name_or_label():
    assert faceit.match_id_of(f"{MATCH_ID}-1-1.dem") == MATCH_ID
    assert faceit.match_id_of(f"{MATCH_ID}-1-1.dem.zst") == MATCH_ID
    assert faceit.match_id_of("inferno.dem", f"week3={MATCH_ID}") == MATCH_ID
    assert faceit.match_id_of("inferno.dem", "week3") is None


def test_parse_competition_esea_name():
    assert faceit.parse_competition("S59 NA Open1-8 East - Regular Season") == {
        "season": 59, "region": "NA", "division": "Open 1-8", "group": "East", "stage": "Regular Season",
    }
    assert faceit.parse_competition("S59 NA Open9-10 West - Playoffs")["division"] == "Open 9-10"
    assert faceit.parse_competition("S59 NA Main A - Regular Season")["division"] == "Main"
    assert faceit.parse_competition("S60 EU Intermediate")["division"] == "Intermediate"
    assert faceit.parse_competition("Some Cup") == {}


def test_match_block_teams_opponents_and_standins():
    block = faceit.match_block({**fake_match(), "organizer_name": "ESEA"}, demo_with_standin())
    assert block["teams"] == ["Alpha", "Bravo"]
    assert block["division"] == "ESEA Open 1-8" and block["league"] == "ESEA" and block["stage"] == "Regular Season"
    assert block["url"] == f"https://www.faceit.com/en/cs2/room/{MATCH_ID}"
    assert block["score"] == {"Alpha": 9, "Bravo": 13} and block["winner"] == "Bravo"
    assert block["players"]["11"] == {"nickname": "a1", "team": "Alpha", "opponent": "Bravo"}
    assert block["players"]["21"] == {"nickname": "b1", "team": "Bravo", "opponent": "Alpha"}
    # not on the FACEIT roster, but on the CT side with Alpha in the demo
    assert block["players"]["12"] == {"nickname": "standin", "team": "Alpha", "opponent": "Bravo"}


def test_fetch_caches_finished_matches_only(tmp_path, monkeypatch):
    calls = []

    def fake_get(path, key):
        calls.append(path)
        return {"name": "ESEA"} if path.startswith("/organizers") else fake_match(status)

    monkeypatch.setattr(faceit, "_get", fake_get)
    status = "ONGOING"
    faceit.fetch_match(MATCH_ID, "k", tmp_path)
    assert not (tmp_path / f"{MATCH_ID}.json").exists()
    status = "FINISHED"
    assert faceit.fetch_match(MATCH_ID, "k", tmp_path)["organizer_name"] == "ESEA"
    calls.clear()
    assert faceit.fetch_match(MATCH_ID, "k", tmp_path)["match_id"] == MATCH_ID
    assert calls == []


def test_lookup_degrades_without_key_or_on_api_error(monkeypatch):
    monkeypatch.delenv("FACEIT_API_KEY", raising=False)
    demo = demo_with_standin()
    assert faceit.lookup(demo, Config()) is None

    def boom(*_):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(faceit, "fetch_match", boom)
    config = Config()
    config.faceit.api_key = "k"
    assert faceit.lookup(demo, config) is None


def test_non_faceit_demo_never_calls_the_api(monkeypatch):
    monkeypatch.setattr(faceit, "fetch_match", lambda *_: (_ for _ in ()).throw(AssertionError("called")))
    config = Config()
    config.faceit.api_key = "k"
    assert faceit.lookup(make_demo([], [make_round(0, 0, 1000)]), config) is None


def test_add_to_report_keeps_the_rest(tmp_path):
    report = tmp_path / "r.json"
    report.write_text(json.dumps({"sequences": []}), encoding="utf-8")
    faceit.add_to_report(report, {"teams": ["A", "B"]})
    assert json.loads(report.read_text(encoding="utf-8")) == {"sequences": [], "match": {"teams": ["A", "B"]}}


def test_division_falls_back_to_competition_name():
    match = {**fake_match(), "competition_name": "BHOP Playoffs A", "organizer_name": "Saltcrew"}
    block = faceit.match_block(match, demo_with_standin())
    assert block["division"] == "BHOP Playoffs A" and "stage" not in block
