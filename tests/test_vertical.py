import shutil
import subprocess
from pathlib import Path

import pytest

from highlights.video.vertical import make_vertical, vertical_filter


def test_filters_per_mode():
    assert "crop=trunc(ih*9/16/2)*2:ih" in vertical_filter("crop")
    blur = vertical_filter("blur")
    assert "boxblur" in blur and "kfsrc" not in blur
    assert "kfsrc" in vertical_filter("blur", killfeed=True)
    with pytest.raises(ValueError):
        vertical_filter("stretch")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
@pytest.mark.parametrize("mode,killfeed", [("blur", False), ("blur", True), ("crop", False)])
def test_make_vertical_outputs_1080x1920(tmp_path, mode, killfeed):
    ffmpeg = Path(shutil.which("ffmpeg"))
    src = tmp_path / "src.mp4"
    subprocess.run(
        [str(ffmpeg), "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=30:duration=1",
         "-f", "lavfi", "-i", "sine=duration=1", "-shortest", "-pix_fmt", "yuv420p", str(src)],
        check=True,
    )
    out = make_vertical(ffmpeg, src, tmp_path / "v.mp4", mode, killfeed)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True,
    )
    assert probe.stdout.strip() == "1080,1920"
