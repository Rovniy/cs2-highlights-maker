from click.testing import CliRunner
from conftest import make_demo
from test_campaths import _sequence
from test_splice import _play

from highlights import cli
from highlights.recording.campaths import load_campaths, parse_campath, retime, write_retimed
from highlights.recording.cinematic import add_cinematic_sequences
from highlights.recording.sequences import build_actions_file
from highlights.recording.splice_plan import coverage


def _keys(path):
    return [(round(k.t, 3), k.pos) for k in path.keyframes]


def _simple(tmp_path):
    from test_splice import write_oriented

    write_oriented(tmp_path / "Nuke", "p", [(100, 0, 0, 0, 0, 0), (104, 40, 0, 0, 0, 0)])
    return load_campaths(tmp_path, "de_nuke")[0]


def test_retime_delays_start_and_holds_last_pose(tmp_path):
    path = _simple(tmp_path)
    assert retime(path).keyframes == path.keyframes
    assert _keys(retime(path, 0, 10)) == [(0, (0, 0, 0)), (4, (40, 0, 0)), (10, (40, 0, 0))]
    assert _keys(retime(path, 2, 10)) == [(0, (0, 0, 0)), (2, (0, 0, 0)), (6, (40, 0, 0)), (10, (40, 0, 0))]
    assert retime(path, 0, 2).duration == 4  # never shortens


def test_written_file_matches_retime_and_original_is_untouched(tmp_path):
    path = _simple(tmp_path)
    before = path.file.read_text()
    for offset, hold in ((0.0, 10.0), (2.0, 10.0), (3.0, None)):
        out = write_retimed(path, offset, hold, tmp_path / "out" / f"r{offset}.campath")
        assert _keys(parse_campath(out)) == _keys(retime(path, offset, hold))
    assert path.file.read_text() == before


def test_hold_extends_cut_in_over_the_whole_clip(tmp_path):
    spec = {"near": [(0, 100, 0, 0, 0, 0), (8, 100, 0, 0, 0, 0)]}  # 8 s path facing the player
    sequence, paths, spatial = _play(tmp_path, spec)
    plain, _ = add_cinematic_sequences([sequence], paths, spatial, 64.0, splice=True)
    assert plain[1].campath_retime is None and coverage(plain[1].splice_segments) < 9
    held, _ = add_cinematic_sequences([sequence], paths, spatial, 64.0, splice=True, hold=True)
    assert held[1].campath_retime == {"offset_seconds": 0.0, "hold_until_seconds": 20.0}
    assert coverage(held[1].splice_segments) > 19


def test_align_finds_a_later_start_that_sees_more(tmp_path):
    # Faces the player for 3 s, then turns away and ends: the last pose sees nothing.
    spec = {"turns": [(0, 0, 0, 0, 0, 0), (3, 0, 0, 0, 0, 0), (3.1, 0, 0, 0, 0, 180), (6, 0, 0, 0, 0, 180)]}
    sequence, paths, spatial = _play(tmp_path, spec)
    held, _ = add_cinematic_sequences([sequence], paths, spatial, 64.0, splice=True, hold=True)
    aligned, _ = add_cinematic_sequences([sequence], paths, spatial, 64.0, splice=True, align=True)
    assert coverage(aligned[1].splice_segments) > coverage(held[1].splice_segments)
    assert aligned[1].campath_retime["offset_seconds"] > 0


def test_append_hold_marks_the_take(tmp_path):
    sequence, paths, spatial = _play(tmp_path, {"p": [(0, 100, 0, 0, 0, 0), (8, 100, 0, 0, 0, 0)]})
    result, _ = add_cinematic_sequences([sequence], paths, spatial, 64.0, hold=True)
    assert result[1].campath_retime == {"offset_seconds": 0.0, "hold_until_seconds": 20.0}
    result, _ = add_cinematic_sequences([sequence], paths, spatial, 64.0)
    assert result[1].campath_retime is None


def test_actions_file_loads_the_retimed_copy(tmp_path):
    from dataclasses import replace

    sequence, paths, spatial = _play(tmp_path / "paths", {"p": [(0, 100, 0, 0, 0, 0), (8, 100, 0, 0, 0, 0)]})
    result, _ = add_cinematic_sequences([sequence], paths, spatial, 64.0, hold=True)
    demo = make_demo([], [], slots={"111": 1})
    from highlights.config import RecordingConfig

    work = tmp_path / "work"
    actions = build_actions_file(tmp_path / "d.dem", result, demo, RecordingConfig(), work)
    text = actions.write().read_text(encoding="utf-8")
    copy = work / "campaths" / f"p_{result[1].start_tick}.campath"
    assert copy.is_file() and "campaths/p_" in text
    assert str(paths[0].file).replace("\\", "/") not in text
    assert parse_campath(copy).duration == 20.0
    plain = build_actions_file(tmp_path / "d.dem", [replace(result[1], campath_retime=None)], demo, RecordingConfig(), work)
    assert str(paths[0].file).replace("\\", "/") in plain.write().read_text(encoding="utf-8")


def test_flags_need_cinematic_and_align_needs_splice(tmp_path):
    demo = tmp_path / "x.dem"
    demo.write_bytes(b"x")
    runner = CliRunner()
    result = runner.invoke(cli.main, ["run", str(demo), "--campath-hold"])
    assert result.exit_code != 0 and "need --cinematic" in result.output
    result = runner.invoke(cli.main, ["run", str(demo), "--cinematic", "--campath-align"])
    assert result.exit_code != 0 and "splice" in result.output
