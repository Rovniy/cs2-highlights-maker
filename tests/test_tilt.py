import shutil
import subprocess

import pytest
from test_splice import _play

from highlights.analysis.tilt import plan_sequences
from highlights.recording.cinematic import add_cinematic_sequences
from highlights.video.splice import splice_filter
from highlights.video.tiltshift import tilt_filter

WIDE = [(0, -300, 0, 400, 26.6, 0), (30, -300, 0, 400, 26.6, 0)]  # ~900u away, 400u up, aimed at the player
CLOSE = [(0, 100, 0, 0, 0, 0), (30, 100, 0, 0, 0, 0)]  # 400u away at the player's height


def _cinematic(tmp_path, points):
    sequence, paths, spatial = _play(tmp_path, {"cam": points})
    result, spatial = add_cinematic_sequences([sequence], paths, spatial, 64.0, splice=True, hold=True)
    assert len(result) == 2 and result[1].splice_segments
    return result, spatial


def test_auto_tilts_wide_elevated_shots_only(tmp_path):
    result, spatial = _cinematic(tmp_path / "w", WIDE)
    planned = plan_sequences(result, spatial, 64.0, "auto")
    assert planned[0].tilt is None and planned[1].tilt
    assert planned[1].tilt["height"] == 400 and planned[1].tilt["distance"] > 700
    result, spatial = _cinematic(tmp_path / "c", CLOSE)
    assert plan_sequences(result, spatial, 64.0, "auto")[1].tilt is None


def test_on_tilts_every_cam_path_shot_and_off_none(tmp_path):
    result, spatial = _cinematic(tmp_path, CLOSE)
    assert plan_sequences(result, spatial, 64.0, "on")[1].tilt["reason"] == "forced"
    assert plan_sequences(result, spatial, 64.0, "off") is result
    assert all(s.tilt is None for s in result)


def test_generated_cams_are_never_tilted(tmp_path):
    from dataclasses import replace

    result, spatial = _cinematic(tmp_path, WIDE)
    generated = replace(result[1], number=3, dynamic_cam=[{"start": 0, "end": 2}])
    planned = plan_sequences([*result, generated], {**spatial, 3: spatial[2]}, 64.0, "on")
    assert planned[1].tilt and planned[2].tilt is None


def test_splice_filter_tilts_only_the_cinematic_input():
    plain = splice_filter([(1.0, 3.0)])
    tilted = splice_filter([(1.0, 3.0)], tilt=(320, 180))
    assert "maskedmerge" not in plain and tilted.startswith("[1:v]") and "gblur" in tilted
    assert "[0:v][cin]overlay=enable='between(t,1.000,3.000)'" in tilted


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_tilt_filter_blurs_the_edges_and_keeps_the_middle_sharp(tmp_path):
    width, height = 320, 180
    raw = subprocess.run(
        [
            "ffmpeg", "-loglevel", "error", "-f", "lavfi",
            "-i", f"nullsrc=s={width}x{height}:d=1:r=1,geq=lum='255*mod(X+Y,2)':cb=128:cr=128",
            "-filter_complex", f"[0:v]{tilt_filter(320, 180)}[v]", "-map", "[v]",
            "-frames:v", "1", "-pix_fmt", "gray", "-f", "rawvideo", "-",
        ],
        capture_output=True, check=True,
    ).stdout
    assert len(raw) == width * height

    def spread(row):
        line = raw[row * width:(row + 1) * width]
        return max(line) - min(line)

    assert spread(height // 2) > 200  # checkerboard untouched in the sharp band
    assert spread(2) < 60 and spread(height - 3) < 60  # blurred towards flat grey


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_tilted_splice_runs_end_to_end(tmp_path):
    from pathlib import Path

    from highlights.video.splice import splice_clip

    ffmpeg = Path(shutil.which("ffmpeg"))
    clips = []
    for name, source in (("pov.mp4", "testsrc"), ("cin.mp4", "smptebars")):
        clip = tmp_path / name
        subprocess.run(
            [str(ffmpeg), "-loglevel", "error", "-f", "lavfi", "-i", f"{source}=size=320x180:rate=30:duration=3",
             "-f", "lavfi", "-i", "sine=duration=3", "-shortest", "-pix_fmt", "yuv420p", str(clip)],
            check=True,
        )
        clips.append(clip)
    out = splice_clip(ffmpeg, clips[0], clips[1], [(1.0, 2.0)], tmp_path / "out.mp4", tilt=(320, 180))
    assert out.stat().st_size > 0
