import json
from pathlib import Path

from highlights.models import Moment
from highlights.scan import KEEP, REMOVE, DemoScan, classify, find_demos, write_scan


def _moment(score: float, round_index: int = 4) -> Moment:
    return Moment(steamid="1", name="FuzzyHat", side="CT", round_index=round_index, score=score)


def test_find_demos_is_case_insensitive_and_optionally_recursive(tmp_path):
    (tmp_path / "a.dem").touch()
    (tmp_path / "b.DEM").touch()
    (tmp_path / "notes.txt").touch()
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.dem").touch()
    assert [p.name for p in find_demos(tmp_path, False)] == ["a.dem", "b.DEM"]
    assert [p.name for p in find_demos(tmp_path, True)] == ["a.dem", "b.DEM", "c.dem"]


def test_classify_keeps_demo_with_a_play_at_the_minimum():
    keep = classify(Path("a.dem"), "de_dust2", [_moment(3.0), _moment(9.5, round_index=6)], 9.5)
    assert (keep.status, keep.best_score, keep.best_round, keep.plays) == (KEEP, 9.5, 7, 1)
    low = classify(Path("b.dem"), "de_dust2", [_moment(3.0)], 9.5)
    assert (low.status, low.plays, low.best_score) == (REMOVE, 0, 3.0)
    none = classify(Path("c.dem"), "de_dust2", [], 9.5)
    assert none.status == REMOVE and "no plays" in none.note


def test_write_scan_lists_paths_under_headings_and_details_json(tmp_path):
    results = [
        DemoScan(tmp_path / "a.dem", KEEP, "de_dust2", 12.0, 5, "4K", 1),
        DemoScan(tmp_path / "b.dem", REMOVE, "de_inferno", note="no plays by these players"),
        DemoScan(tmp_path / "c.dem", "unreadable", note="bad file"),
    ]
    out = tmp_path / "scan.txt"
    details = write_scan(results, out, 8.0, ["FuzzyHat"])
    text = out.read_text(encoding="utf-8")
    keep_at, remove_at = text.index("# KEEP"), text.index("# REMOVE")
    assert keep_at < text.index(str(tmp_path / "a.dem")) < remove_at < text.index(str(tmp_path / "b.dem"))
    assert str(tmp_path / "c.dem") in text.split("# UNREADABLE")[1]
    data = json.loads(details.read_text(encoding="utf-8"))
    assert details.name == "scan.json" and data["min_score"] == 8.0
    assert [d["status"] for d in data["demos"]] == ["keep", "remove", "unreadable"]


def test_safe_event_turns_a_list_result_into_an_empty_frame():
    # demoparser2 returns [] (not a DataFrame) for an event the demo never fires
    from highlights.demo.parser import _safe_event

    class Stub:
        def __init__(self, result):
            self.result = result

        def parse_event(self, name, **kwargs):
            return self.result

    for result in ([], None):
        df = _safe_event(Stub(result), "player_blind")
        assert df.empty and list(df.iterrows()) == []


def test_delete_demos_removes_only_the_remove_verdicts(tmp_path):
    from highlights.scan import delete_demos

    files = {name: tmp_path / f"{name}.dem" for name in ("keep", "remove", "bad", "gone")}
    for name in ("keep", "remove", "bad"):
        files[name].touch()
    results = [
        DemoScan(files["keep"], KEEP),
        DemoScan(files["remove"], REMOVE),
        DemoScan(files["bad"], "unreadable"),
        DemoScan(files["gone"], REMOVE),  # already missing -> reported, not raised
    ]
    deleted, failed = delete_demos(results)
    assert deleted == [files["remove"]]
    assert [p for p, _ in failed] == [files["gone"]]
    assert files["keep"].exists() and files["bad"].exists() and not files["remove"].exists()


def test_match_summary_drops_roster_and_picks_the_scanned_players_team():
    from highlights.scan import match_cell, match_summary

    block = {
        "teams": ["Alpha", "Bravo"], "division": "Open",
        "players": {"11": {"nickname": "a1", "team": "Alpha", "opponent": "Bravo"},
                    "21": {"nickname": "b1", "team": "Bravo", "opponent": "Alpha"}},
    }
    mine = match_summary(block, ["99", "21"])
    assert "players" not in mine and mine["team"] == "Bravo" and mine["opponent"] == "Alpha"
    assert match_cell(mine) == "Bravo vs Alpha · Open"
    assert match_cell(match_summary(block, None)) == "Alpha vs Bravo · Open"
    assert match_summary(None, ["11"]) is None and match_cell(None) == ""


def test_write_scan_includes_match(tmp_path):
    results = [DemoScan(tmp_path / "a.dem", KEEP, "de_dust2", 12.0, 5, "4K", 1, match={"teams": ["A", "B"]})]
    details = write_scan(results, tmp_path / "scan.txt", 8.0, [])
    assert json.loads(details.read_text(encoding="utf-8"))["demos"][0]["match"] == {"teams": ["A", "B"]}
