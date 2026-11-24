from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import click
import pytest
from click.testing import CliRunner

from test_campaths import _sequence

from highlights.cli import _parse_cam_styles, _splice_cinematics, main
from highlights.models import CameraAction, Kill, Moment
from highlights.recording.dyn_sequences import add_dynamic_sequences

DEMO = SimpleNamespace(tickrate=64.0)


def pov(number, start=6400, end=7360, kill_tick=None):
    """15 s play; one kill at `kill_tick` (default 8 s in)."""
    sequence = _sequence(number, start, end)
    kill_tick = kill_tick or start + 8 * 64
    kill = Kill(
        tick=kill_tick, round_index=0, attacker_steamid="111", attacker_name="a", attacker_side="T",
        victim_steamid="222", victim_name="v", weapon="ak47",
    )
    sequence.moments = [Moment(steamid="111", name="a", side="T", round_index=0, kills=[kill])]
    return sequence


def straight_sampler(demo, requests):
    """A player running along +x at 100 u/s, looking along +x."""
    out = {}
    for number, (sequence, ranges) in requests.items():
        out[number] = {"111": [(i / 16, (100 * i / 16, 0.0, 0.0), 0.0, 0.0) for i in range(0, 16 * 16)]}
    return out


def test_opener_take_follows_its_pov_and_everything_is_renumbered(tmp_path):
    sequences = [pov(1), pov(2, 20000, 20960)]
    spatial = {1: {"tag": "one"}, 2: {"tag": "two"}}
    result, new_spatial = add_dynamic_sequences(
        sequences, spatial, DEMO, straight_sampler, tmp_path, ["face"], None
    )
    assert [s.number for s in result] == [1, 2, 3, 4]
    assert [s.cinematic_of for s in result] == [None, 1, None, 3]
    take = result[1]
    assert take.splice_segments[0][0] == 0.0 and take.dynamic_cam[0]["kind"] == "intro"
    assert take.campath.exists() and take.campath.suffix == ".campath"
    assert new_spatial[2] == {"tag": "one"} and new_spatial[3] == {"tag": "two"}


def test_plays_without_any_segment_get_no_take(tmp_path):
    sequence = pov(1, kill_tick=6400 + 2 * 64)  # kill 2 s in: no room for an opener
    result, _ = add_dynamic_sequences([sequence], {}, DEMO, straight_sampler, tmp_path, ["face"], None)
    assert len(result) == 1


def test_downtime_skips_ranges_a_real_splice_take_covers(tmp_path):
    base = pov(1)
    real = replace(base, number=2, cinematic_of=1, campath=Path("x.dz"), splice_segments=[(9.5, 14.5)])
    result, _ = add_dynamic_sequences([base, real], {}, DEMO, straight_sampler, tmp_path, None, ["tps"])
    take = result[-1]
    assert take.dynamic_cam
    assert all(not (s["start_seconds"] < 14.5 and s["end_seconds"] > 9.5) for s in take.dynamic_cam)
    assert [s.cinematic_of for s in result] == [None, 1, 1]  # real take kept, generated one after it


def test_unreadable_demo_skips_generation(tmp_path):
    result, _ = add_dynamic_sequences([pov(1)], {}, DEMO, lambda d, r: {}, tmp_path, ["face"], None)
    assert len(result) == 1


def test_cam_style_option_parsing_and_cli_conflict():
    assert _parse_cam_styles(None) is None
    assert _parse_cam_styles("face,tps") == ["face", "tps"]
    with pytest.raises(click.BadParameter):
        _parse_cam_styles("drone")
    runner = CliRunner()
    result = runner.invoke(main, ["run", __file__, "--suggest-cinematic", "--intro-cam", "face"])
    assert result.exit_code != 0 and "cannot be combined" in result.output


def test_splice_chain_applies_real_take_first_then_generated(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        "highlights.video.splice.splice_clip",
        lambda ffmpeg, clip, take, segments, out, crf, tilt=False: calls.append((clip.name, take.name, segments)) or out,
    )
    def rec(sequence, name):
        return SimpleNamespace(sequence=sequence), Path(name)
    base = pov(1)
    real = replace(base, number=2, cinematic_of=1, campath=Path("a.dz"), splice_segments=[(5.0, 7.0)])
    generated = replace(base, number=3, cinematic_of=1, campath=Path("g.campath"), splice_segments=[(0.0, 3.0)], dynamic_cam=[{}])
    appended = replace(base, number=4, cinematic_of=1, campath=Path("b.dz"))  # append-mode take, no segments
    recorded = [rec(base, "pov.mp4"), rec(generated, "gen.mp4"), rec(real, "real.mp4"), rec(appended, "app.mp4")]
    result = _splice_cinematics(Path("ffmpeg"), recorded, tmp_path, 18)
    assert [c[1] for c in calls] == ["real.mp4", "gen.mp4"]
    assert calls[1][0] == "spliced_01_0.mp4"  # the generated take overlays the already spliced clip
    assert [o.sequence.number for o, _ in result] == [1, 4]


def test_intro_transition_is_recorded_per_intro_segment(tmp_path):
    result, _ = add_dynamic_sequences(
        [pov(1)], {}, DEMO, straight_sampler, tmp_path, ["face"], ["tps"], intro_transition="cut"
    )
    cams = result[1].dynamic_cam
    assert [c.get("transition") for c in cams if c["kind"] == "intro"] == ["cut"]
    assert all("transition" not in c for c in cams if c["kind"] == "downtime")
    default, _ = add_dynamic_sequences([pov(1)], {}, DEMO, straight_sampler, tmp_path, ["face"], None)
    assert default[1].dynamic_cam[0]["transition"] == "swoop"


def test_intro_transition_option_reaches_run_and_batch():
    for command in (["run", "--help"], ["batch", "--help"]):
        out = CliRunner().invoke(main, command).output
        assert "--intro-transition [swoop|cut]" in out
