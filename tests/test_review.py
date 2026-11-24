import json
import shutil
import subprocess
from pathlib import Path

import pytest


def test_frame_problem_spots_dark_and_featureless_frames():
    from highlights.video.review import FRAME_H, FRAME_W, frame_problem

    size = FRAME_W * FRAME_H
    assert frame_problem(bytes([2]) * size) == "dark"
    assert frame_problem(bytes([128]) * size) == "flat"
    checker = bytes((x + y) % 2 * 200 + 20 for y in range(FRAME_H) for x in range(FRAME_W))
    assert frame_problem(checker) is None


def test_suspect_ranges_only_inside_checked_ranges_and_need_a_run():
    from highlights.video.review import suspect_ranges

    problems = [None, "flat", "flat", "flat", None, "dark", None, "dark", "dark"]
    found = suspect_ranges(problems, [(0.0, 1.5)], fps=4)
    assert found == [{"start_seconds": 0.25, "end_seconds": 1.0, "reason": "flat"}]
    assert suspect_ranges(problems, [], fps=4) == []


def test_checked_ranges_come_from_cam_path_parts_only():
    from highlights.video.review import checked_ranges

    report = {
        "sequences": [
            {"number": 1, "cinematic": None},
            {"number": 2, "cinematic": {"splice_segments": [{"start_seconds": 4.0, "end_seconds": 6.5}]}},
            {"number": 3, "cinematic": {"splice_segments": [{"start_seconds": 0.0, "end_seconds": 3.0}]}},
        ]
    }
    assert checked_ranges(report, 20.0) == [(0.0, 3.0), (4.0, 6.5)]
    assert checked_ranges({"kind": "buildup"}, 6.0) == [(0.0, 6.0)]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_review_dir_flags_a_flat_cam_path_and_writes_sheets(tmp_path):
    from highlights.video.review import review_dir

    clip = tmp_path / "a-player-1.mp4"
    # 2 s of test pattern, then 2 s of flat grey (a "camera inside a wall").
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=2",
         "-f", "lavfi", "-i", "color=c=gray:size=320x180:rate=30:duration=2",
         "-filter_complex", "[0:v][1:v]concat=n=2:v=1[v]", "-map", "[v]", "-pix_fmt", "yuv420p", str(clip)],
        check=True,
    )
    report = {"sequences": [{"cinematic": {"splice_segments": [{"start_seconds": 1.0, "end_seconds": 4.0}]}}]}
    clip.with_suffix(".json").write_text(json.dumps(report))
    [result] = review_dir(Path(shutil.which("ffmpeg")), tmp_path)
    assert result["suspect_ranges"] and result["suspect_ranges"][0]["start_seconds"] >= 1.9
    assert (tmp_path / "review" / "a-player-1.jpg").stat().st_size > 0
    assert "a-player-1" in (tmp_path / "review" / "index.html").read_text()
    assert json.loads(clip.with_suffix(".json").read_text())["review"]["suspect_ranges"]
