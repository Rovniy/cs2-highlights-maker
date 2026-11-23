import shutil
import subprocess
from pathlib import Path

import pytest

from highlights.video.splice import fade_filter, splice_clip

FFMPEG = shutil.which("ffmpeg")


def test_filter_names_the_windows_and_skips_zero_length_ramps():
    graph = fade_filter([(2.0, 4.0, 0.5, 0.5), (8.0, 9.0, 0.0, 0.3)])
    assert "split=2[x0][x1]" in graph
    assert "trim=start=1.500:end=4.500" in graph and "fade=t=in:st=1.500:d=0.500:alpha=1" in graph
    assert "fade=t=out:st=4.000:d=0.500:alpha=1" in graph
    second = graph.split("[x1]trim")[1]
    assert "fade=t=in" not in second and "fade=t=out:st=9.000:d=0.300" in second
    assert graph.endswith("format=yuv420p[v]")
    assert "split" not in fade_filter([(2.0, 4.0, 0.5, 0.5)])


def _solid(ffmpeg, colour, path, with_audio):
    args = [ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={colour}:s=64x36:r=30:d=6"]
    if with_audio:
        args += ["-f", "lavfi", "-i", "sine=frequency=440:d=6", "-c:a", "aac"]
    subprocess.run([*args, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "10", str(path)], check=True)


def _red_blue(ffmpeg, path, t):
    """(red, blue) of the middle pixel of the frame at t seconds."""
    raw = subprocess.run(
        [ffmpeg, "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1", "-vf", "crop=1:1:32:18,format=rgb24", "-f", "rawvideo", "-"],
        capture_output=True,
        check=True,
    ).stdout
    return raw[0], raw[2]


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not installed")
def test_xray_dissolves_in_holds_and_dissolves_out(tmp_path):
    pov, xray, out = tmp_path / "pov.mp4", tmp_path / "xray.mp4", tmp_path / "out.mp4"
    _solid(FFMPEG, "red", pov, True)
    _solid(FFMPEG, "blue", xray, False)
    splice_clip(Path(FFMPEG), pov, xray, [(2.0, 4.0)], out, crf=10, fades=[(2.0, 4.0, 0.5, 0.5)])

    at = lambda t: _red_blue(FFMPEG, out, t)  # noqa: E731
    red, blue = at(1.0)
    assert red > 200 and blue < 60  # before the ramp: the POV clip
    red, blue = at(1.75)
    assert 60 < red < 200 and 60 < blue < 200  # half way up the ramp: both show
    red, blue = at(3.0)
    assert blue > 200 and red < 60  # the hold: x-ray
    red, blue = at(4.25)
    assert 60 < red < 200 and 60 < blue < 200  # ramping out
    red, blue = at(5.5)
    assert red > 200 and blue < 60  # POV again
    audio = subprocess.run(
        [str(Path(FFMPEG).with_name("ffprobe")), "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(out)],
        capture_output=True,
        text=True,
    ).stdout
    assert "aac" in audio  # the POV audio is kept


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not installed")
def test_window_at_the_clip_start_and_two_windows(tmp_path):
    pov, xray, out = tmp_path / "pov.mp4", tmp_path / "xray.mp4", tmp_path / "out.mp4"
    _solid(FFMPEG, "red", pov, True)
    _solid(FFMPEG, "blue", xray, False)
    splice_clip(Path(FFMPEG), pov, xray, [(0.0, 1.0), (3.0, 4.0)], out, crf=10, fades=[(0.0, 1.0, 0.0, 0.4), (3.0, 4.0, 0.4, 0.4)])
    assert _red_blue(FFMPEG, out, 0.1)[1] > 200  # x-ray from the first frame, no ramp in
    assert _red_blue(FFMPEG, out, 2.0)[0] > 200  # between the windows: POV
    assert _red_blue(FFMPEG, out, 3.5)[1] > 200  # second window
    assert _red_blue(FFMPEG, out, 5.5)[0] > 200
    length = subprocess.run(
        [str(Path(FFMPEG).with_name("ffprobe")), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
        capture_output=True,
        text=True,
    ).stdout
    assert float(length) == pytest.approx(6.0, abs=0.1)  # the POV timeline and length are kept
