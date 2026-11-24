import json

from conftest import make_demo, make_kill, make_round

from highlights.config import RecordingConfig
from highlights.models import CameraAction, Sequence
from highlights.recording.campaths import load_campaths, rank_campaths
from highlights.recording.cinematic import add_cinematic_sequences
from highlights.recording.sequences import build_actions_file


def write_path(folder, name, points):
    """Write a minimal campath with generated points (never real pack data)."""
    folder.mkdir(parents=True, exist_ok=True)
    body = "".join(
        f'<p t="{t}" x="{x}" y="{y}" z="{z}" fov="90" rx="0" ry="0" rz="0"/>' for t, x, y, z in points
    )
    file = folder / f"{name}.dz"
    file.write_text(f'<?xml version="1.0"?><campath><points>{body}</points></campath>')
    return file


def _sequence(number=1, start=5000, end=6000):
    return Sequence(
        number=number,
        start_tick=start,
        end_tick=end,
        cameras=[CameraAction(tick=start, steamid="111")],
        label="play",
        score=1.0,
        round_index=0,
    )


def _spatial(x, y, z):
    return {
        "engagements": [
            {"is_selected_moment_kill": True, "engagement_center": {"x": x, "y": y, "z": z}},
            {"is_selected_moment_kill": False, "engagement_center": {"x": 9e5, "y": 9e5, "z": 0}},
        ]
    }


def test_load_matches_map_folder_and_skips_bad_files(tmp_path):
    write_path(tmp_path / "Nuke", "near", [(0, 0, 0, 0), (10, 100, 0, 0)])
    write_path(tmp_path / "Mirage", "other_map", [(0, 0, 0, 0), (10, 1, 0, 0)])
    (tmp_path / "Nuke" / "broken.dz").write_text("<campath>")
    write_path(tmp_path / "Nuke", "single", [(0, 0, 0, 0)])
    paths = load_campaths(tmp_path, "de_nuke")
    assert [p.name for p in paths] == ["near"]
    assert paths[0].duration == 10
    assert load_campaths(tmp_path / "missing", "de_nuke") == []


def test_rank_prefers_path_closest_to_action(tmp_path):
    write_path(tmp_path / "de_nuke", "far", [(0, 5000, 5000, 0), (5, 5100, 5000, 0)])
    write_path(tmp_path / "de_nuke", "close", [(0, 90, 0, 0), (5, 110, 0, 0)])
    paths = load_campaths(tmp_path, "de_nuke")
    ranked = rank_campaths(paths, [(100.0, 0.0, 0.0)])
    assert [p.name for p in ranked] == ["close", "far"]


def test_add_cinematic_interleaves_and_renumbers(tmp_path):
    write_path(tmp_path / "Nuke", "a", [(0, 0, 0, 0), (30, 10, 0, 0)])
    write_path(tmp_path / "Nuke", "b", [(0, 900, 0, 0), (30, 910, 0, 0)])
    paths = load_campaths(tmp_path, "de_nuke")
    sequences = [_sequence(1), _sequence(2, 20000, 21000)]
    spatial = {1: _spatial(0, 0, 0), 2: _spatial(900, 0, 0)}

    result, new_spatial = add_cinematic_sequences(sequences, paths, spatial, 64.0, takes=1)

    assert [s.number for s in result] == [1, 2, 3, 4]
    assert [s.cinematic_of for s in result] == [None, 1, None, 3]
    assert [s.campath.stem if s.campath else None for s in result] == [None, "a", None, "b"]
    assert result[1].start_tick == 5000 and result[1].end_tick == 6000
    assert set(new_spatial) == {1, 2, 3, 4}
    assert new_spatial[4] is spatial[2]


def test_cinematic_takes_limits_and_no_spatial_means_no_take(tmp_path):
    for name, x in (("a", 0), ("b", 500), ("c", 900)):
        write_path(tmp_path / "Nuke", name, [(0, x, 0, 0), (30, x, 1, 0)])
    paths = load_campaths(tmp_path, "de_nuke")
    result, _ = add_cinematic_sequences([_sequence(1)], paths, {1: _spatial(0, 0, 0)}, 64.0, takes=2)
    assert len(result) == 3
    result, _ = add_cinematic_sequences([_sequence(1)], paths, {}, 64.0)
    assert len(result) == 1


def test_actions_load_path_only_for_cinematic_sequences(tmp_path):
    file = write_path(tmp_path / "Nuke", "a", [(0, 0, 0, 0), (30, 10, 0, 0)])
    demo = make_demo([make_kill(5000, attacker="111")], [make_round(0, 0, 100000)], slots={"111": 3})
    pov = _sequence(1)
    cinematic = Sequence(**{**pov.__dict__, "number": 2, "campath": file.resolve(), "cinematic_of": 1})

    plain = build_actions_file(tmp_path / "m.dem", [pov], demo, RecordingConfig(), tmp_path / "w")
    assert "campath" not in json.dumps(plain._current)

    actions = build_actions_file(tmp_path / "m.dem", [pov, cinematic], demo, RecordingConfig(), tmp_path / "w")
    cmds = [a["cmd"] for group in json.loads(actions.write().read_text()) for a in group["actions"]]
    loads = [c for c in cmds if c.startswith("mirv_campath load")]
    assert len(loads) == 1 and "a.dz" in loads[0] and "\\" not in loads[0]
    assert cmds.index("mirv_campath edit start") > cmds.index(loads[0])
    assert cmds.count("mirv_campath clear") == 2  # reset at the setup of each sequence


def test_cinematic_kill_feed_is_blocked_but_pov_feed_is_not(tmp_path):
    file = write_path(tmp_path / "Nuke", "a", [(0, 0, 0, 0), (30, 10, 0, 0)])
    demo = make_demo([make_kill(5000, attacker="111")], [make_round(0, 0, 100000)], slots={"111": 3})
    pov = _sequence(1)
    cinematic = Sequence(**{**pov.__dict__, "number": 2, "campath": file.resolve(), "cinematic_of": 1})
    actions = build_actions_file(tmp_path / "m.dem", [pov, cinematic], demo, RecordingConfig(), tmp_path / "w")
    groups = json.loads(actions.write().read_text())
    pov_cmds = [a["cmd"] for a in groups[0]["actions"]]
    cine_cmds = [a["cmd"] for a in groups[1]["actions"]]
    assert any("attackerMatch=!x111" in c for c in pov_cmds)
    assert not any("attackerMatch=!x111" in c for c in cine_cmds)
    assert any("attackerMatch=!x0 block=1" in c for c in cine_cmds)
    assert not any("localPlayer" in c for c in cine_cmds)
