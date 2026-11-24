"""Tests for highlights.places: grid lookup, persistence and routes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from highlights import places
from highlights.places import (
    CELL,
    Z_CELL,
    PlaceMap,
    compress_route,
    load_places,
    places_dir,
    places_path,
    route_of,
    save_places,
)


def test_add_and_lookup_same_cell():
    """A query at or inside the sampled point's cell returns that place."""
    pm = PlaceMap("de_mirage")
    pm.add(100.0, 200.0, 0.0, "Catwalk")
    assert pm.lookup(100.0, 200.0, 0.0) == "Catwalk"
    # Another point inside the same cell.
    assert pm.lookup(100.0 + CELL * 0.5, 200.0 + CELL * 0.5, 0.0) == "Catwalk"


def test_most_common_wins_with_alphabetical_tiebreak():
    """Within a cell the most common name wins; ties go alphabetically."""
    pm = PlaceMap("de_mirage")
    pm.add_many([(0.0, 0.0, 0.0, "BombsiteA")] * 3)
    pm.add_many([(0.0, 0.0, 0.0, "Catwalk")] * 2)
    assert pm.lookup(0.0, 0.0, 0.0) == "BombsiteA"

    tie = PlaceMap("de_mirage")
    tie.add(0.0, 0.0, 0.0, "Window")
    tie.add(0.0, 0.0, 0.0, "Apartments")
    assert tie.lookup(0.0, 0.0, 0.0) == "Apartments"


def test_lookup_finds_neighbour_but_not_beyond_radius():
    """Neighbouring cells within SEARCH_CELLS resolve; farther ones do not."""
    pm = PlaceMap("de_nuke")
    pm.add(0.5 * CELL, 0.5 * CELL, 0.5 * Z_CELL, "Ramp")
    # One cell away horizontally: still in range.
    assert pm.lookup(1.5 * CELL, 0.5 * CELL, 0.5 * Z_CELL) == "Ramp"
    # Far beyond the search radius.
    assert pm.lookup(0.5 * CELL + (places.SEARCH_CELLS + 2) * CELL, 0.5 * CELL, 0.5 * Z_CELL) is None


def test_floors_stay_apart():
    """A ground-floor name is not returned several floors up, but a nearby
    vertical neighbour still resolves."""
    pm = PlaceMap("de_nuke")
    pm.add(0.5 * CELL, 0.5 * CELL, 0.0, "Lobby")
    assert pm.lookup(0.5 * CELL, 0.5 * CELL, 4 * Z_CELL) is None
    assert pm.lookup(0.5 * CELL, 0.5 * CELL, Z_CELL * 0.5) == "Lobby"


def test_nearest_cell_wins():
    """With two populated cells, the query resolves to the closer one."""
    pm = PlaceMap("de_inferno")
    pm.add(0.5 * CELL, 0.5 * CELL, 0.5 * Z_CELL, "Banana")
    pm.add(3.5 * CELL, 0.5 * CELL, 0.5 * Z_CELL, "Arch")
    assert pm.lookup(1.2 * CELL, 0.5 * CELL, 0.5 * Z_CELL) == "Banana"
    assert pm.lookup(2.8 * CELL, 0.5 * CELL, 0.5 * Z_CELL) == "Arch"


def test_blank_places_ignored_and_len_counts_cells():
    """None/empty/whitespace names are dropped; len counts populated cells."""
    pm = PlaceMap("de_dust2")
    pm.add_many(
        [
            (0.0, 0.0, 0.0, None),
            (0.0, 0.0, 0.0, ""),
            (0.0, 0.0, 0.0, "   "),
            (CELL, CELL, 0.0, " Long A "),
        ]
    )
    assert len(pm) == 1
    assert pm.lookup(CELL, CELL, 0.0) == "Long A"


def test_to_dict_from_dict_round_trip():
    """A snapshot round trip preserves lookups; wrong grid size raises."""
    pm = PlaceMap("de_ancient")
    pm.add_many([(0.0, 0.0, 0.0, "Temple"), (3.0 * CELL, 0.0, 0.0, "Water")])
    restored = PlaceMap.from_dict(pm.to_dict())
    assert restored.map_name == "de_ancient"
    assert restored.lookup(0.0, 0.0, 0.0) == "Temple"
    assert restored.lookup(3.0 * CELL, 0.0, 0.0) == "Water"
    # Merging accumulates counts naturally.
    restored.add(0.0, 0.0, 0.0, "Water")
    restored.add(0.0, 0.0, 0.0, "Water")
    assert restored.lookup(0.0, 0.0, 0.0) == "Water"

    bad = json.loads(json.dumps(pm.to_dict()))
    bad["cell"] = CELL + 1
    with pytest.raises(ValueError):
        PlaceMap.from_dict(bad)


def test_save_and_load_round_trip(tmp_path: Path):
    """save_places/load_places round trips through JSON on disk."""
    pm = PlaceMap("de_overpass")
    pm.add(10.0, 20.0, 30.0, "Bathhouse")
    path = save_places(pm, tmp_path)
    assert path.is_file()
    loaded = load_places("de_overpass", tmp_path)
    assert loaded is not None
    assert loaded.lookup(10.0, 20.0, 30.0) == "Bathhouse"


def test_load_missing_and_corrupt_return_none(tmp_path: Path):
    """Missing or invalid files load as None instead of raising."""
    assert load_places("nope", tmp_path) is None
    (tmp_path / "broken.json").write_text("not json {{{", encoding="utf-8")
    assert load_places("broken", tmp_path) is None


def test_places_path_lowercases_map_name(tmp_path: Path):
    """The file name is the lowercased map name."""
    assert places_path("De_Nuke", tmp_path) == tmp_path / "de_nuke.json"


def test_places_dir_uses_app_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """places_dir lives under the app data directory, resolved at call time."""
    monkeypatch.setattr(places, "app_data_dir", lambda: tmp_path)
    assert places_dir() == tmp_path / "places"


def test_compress_route_collapses_consecutive_duplicates():
    """Nones and runs collapse; a non-consecutive repeat survives."""
    assert compress_route(["A", "A", None, "B", "A"]) == ["A", "B", "A"]
    assert compress_route([None, "", "  "]) == []


def test_route_of_walks_and_compresses():
    """route_of labels points and skips unknown ones."""
    pm = PlaceMap("de_mirage")
    pm.add_many([(0.5 * CELL, 0.5 * CELL, 0.5 * Z_CELL, "Palace")])
    pm.add_many([(5.5 * CELL, 0.5 * CELL, 0.5 * Z_CELL, "Mid")] * 2)
    points = [
        (0.5 * CELL, 0.5 * CELL, 0.5 * Z_CELL),
        (0.6 * CELL, 0.5 * CELL, 0.5 * Z_CELL),
        (5000.0, 5000.0, 5000.0),  # nowhere
        (5.5 * CELL, 0.5 * CELL, 0.5 * Z_CELL),
        (5.4 * CELL, 0.5 * CELL, 0.5 * Z_CELL),
        (0.5 * CELL, 0.5 * CELL, 0.5 * Z_CELL),
    ]
    assert route_of(points, pm) == ["Palace", "Mid", "Palace"]
