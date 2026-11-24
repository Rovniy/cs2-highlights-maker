from highlights.models import CameraAction
from highlights.places import PlaceMap
from highlights.recording.campaths import load_campaths, map_folders, parse_campath
from highlights.recording.cinematic import _describe, add_cinematic_sequences
from highlights.recording.path_routes import load_labels, path_route
from test_campaths import _sequence, _spatial, write_path
from test_splice import _play


def _straight_path(tmp_path, name="p", duration=10.0, x_end=1000.0, z=0.0):
    """Three keyframes along +x from the origin (generated data only)."""
    file = write_path(
        tmp_path,
        name,
        [(0, 0, 0, z), (duration / 2, x_end / 2, 0, z), (duration, x_end, 0, z)],
    )
    return parse_campath(file)


def test_path_route_labels_positions_and_compresses_duplicates(tmp_path):
    """A straight path through two labelled stretches yields both names, no repeats."""
    path = _straight_path(tmp_path)
    place_map = PlaceMap("de_nuke")
    for x in range(0, 500, 50):
        place_map.add(float(x), 0.0, 0.0, "Catwalk")
    for x in range(500, 1001, 50):
        place_map.add(float(x), 0.0, 0.0, "ASite")
    assert path_route(path, place_map) == ["Catwalk", "ASite"]


def test_path_route_skips_unknown_and_includes_the_final_point(tmp_path):
    """Unlabelled positions drop out; the last point counts even off the step grid."""
    path = _straight_path(tmp_path, duration=10.25, x_end=1025.0)  # 10.25 is no multiple of 0.5
    place_map = PlaceMap("de_nuke")
    place_map.add(0.0, 0.0, 0.0, "Spawn")  # only the very start and the very end are known
    place_map.add(1025.0, 0.0, 0.0, "BombsiteB")
    assert path_route(path, place_map) == ["Spawn", "BombsiteB"]


def test_load_labels_matches_map_and_keeps_string_values(tmp_path):
    """Map folder matches by de_-stripped name; keys lowercased; junk values skipped."""
    (tmp_path / "Nuke").mkdir()
    (tmp_path / "Nuke" / "labels.toml").write_text(
        'B5 = "  Roof to mini sweep  "\nempty = "   "\nnumber = 3\n[list]\na = "x"\n',
        encoding="utf-8",
    )
    assert load_labels(tmp_path, "de_nuke") == {"b5": "Roof to mini sweep"}


def test_load_labels_missing_and_invalid_files_return_empty(tmp_path):
    """No file, invalid TOML, or a missing base all give {} without raising."""
    (tmp_path / "Nuke").mkdir()
    assert load_labels(tmp_path, "de_nuke") == {}
    (tmp_path / "Nuke" / "labels.toml").write_text("this is = = not toml", encoding="utf-8")
    assert load_labels(tmp_path, "de_nuke") == {}
    assert load_labels(tmp_path / "missing", "de_nuke") == {}


def test_map_folders_matches_map_and_ignores_others(tmp_path):
    """Only folders named like the map, sorted; load_campaths still finds its paths."""
    write_path(tmp_path / "Nuke", "a", [(0, 0, 0, 0), (10, 100, 0, 0)])
    write_path(tmp_path / "de_nuke", "b", [(0, 0, 0, 0), (10, 100, 0, 0)])
    write_path(tmp_path / "Mirage", "other", [(0, 0, 0, 0), (10, 1, 0, 0)])
    (tmp_path / "notes.txt").write_text("not a folder", encoding="utf-8")
    folders = map_folders(tmp_path, "de_nuke")
    assert [f.name for f in folders] == ["Nuke", "de_nuke"]
    assert [p.name for p in load_campaths(tmp_path, "de_nuke")] == ["a", "b"]
    assert map_folders(tmp_path / "missing", "de_nuke") == []


def test_candidates_carry_route_and_label(tmp_path):
    """Candidate dicts gain route/label when given, and neutral values when not."""
    for name, y in (("a", 0), ("b", 500)):
        write_path(tmp_path / "Nuke", name, [(0, 0, y, 0), (30, 0, y, 0)])
    paths = load_campaths(tmp_path, "de_nuke")
    place_map = PlaceMap("de_nuke")
    place_map.add(0.0, 0.0, 0.0, "Catwalk")
    place_map.add(0.0, 500.0, 0.0, "Pit")
    # The lookup searches two cells around the point's own cell, so label a wide
    # stretch around each path (x=0..500 is three empty cells away from path b).
    for x in range(-150, 651, 50):
        place_map.add(float(x), 500.0, 0.0, "Pit")
    labels = {"a": "Catwalk sweep"}

    result, _ = add_cinematic_sequences(
        [_sequence(1)], paths, {1: _spatial(0, 0, 0)}, 64.0, takes=1,
        place_map=place_map, labels=labels,
    )
    candidates = result[1].cinematic_candidates
    by_name = {c["name"]: c for c in candidates}
    assert by_name["a"]["route"] == ["Catwalk"]
    assert by_name["a"]["label"] == "Catwalk sweep"
    assert by_name["b"]["route"] == ["Pit"]
    assert by_name["b"]["label"] is None

    result, _ = add_cinematic_sequences([_sequence(1)], paths, {1: _spatial(0, 0, 0)}, 64.0, takes=1)
    for c in result[1].cinematic_candidates:
        assert c["route"] == [] and c["label"] is None

    # Splice mode: candidates carry the same keys.
    sequence, splice_paths, splice_spatial = _play(
        tmp_path / "splice",
        {"seen": [(0, 100, 0, 0, 0, 0), (30, 100, 0, 0, 0, 0)]},  # 500u ahead, sees the player
    )
    result, _ = add_cinematic_sequences(
        [sequence], splice_paths, splice_spatial, 64.0,
        splice=True, place_map=place_map, labels=labels,
    )
    assert len(result) == 2
    for c in result[1].cinematic_candidates:
        assert "route" in c and "label" in c


def test_describe_formats_label_and_route_and_omits_when_absent():
    """NAME "label" (123u, sees player 1.8s) [Silo > Outside > Mini], parts optional."""
    full = {
        "name": "B5",
        "distance": 123.4,
        "coverage_seconds": 1.8,
        "route": ["Silo", "Outside", "Mini"],
        "label": "Roof to mini sweep",
    }
    assert _describe([full]) == 'B5 "Roof to mini sweep" (123u, sees player 1.8s) [Silo > Outside > Mini]'
    bare = {"name": "B5", "distance": 123.4, "coverage_seconds": None, "route": [], "label": None}
    assert _describe([bare]) == "B5 (123u)"


def test_candidate_log_keeps_bracketed_routes_visible(monkeypatch):
    """info() renders rich markup, so bracketed routes (even one-place ones) must be escaped."""
    from rich.console import Console

    from highlights.recording import cinematic
    from highlights.util import log

    recorder = Console(record=True, width=300)
    monkeypatch.setattr(log, "console", recorder)
    candidates = [{"name": "S3", "distance": 10.0, "coverage_seconds": None, "route": ["Tunnels"], "label": None}]
    cinematic.info(cinematic.escape(f"R3 [route: Mini]: cinematic candidates — {cinematic._describe(candidates)}"))
    text = recorder.export_text()
    assert "[route: Mini]" in text
    assert "[Tunnels]" in text
