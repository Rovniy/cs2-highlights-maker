from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import make_demo, make_kill, make_round

from highlights.models import CameraAction, Moment, Sequence
from highlights.teaser import choose_window, output_fps_for, plan_takes, slow_args, take_report


def test_choose_window_takes_the_segment_at_the_kills_and_centres_on_them():
    segments = [(0.0, 2.0), (4.0, 12.0), (14.0, 16.0)]
    assert choose_window(segments, [9.0], 3.0) == (7.5, 10.5)
    assert choose_window(segments, [1.0], 3.0) == (0.0, 2.0)  # shorter than the length: all of it
    assert choose_window(segments, [11.8], 3.0) == (9.0, 12.0)  # clamped to the segment end
    assert choose_window(segments, [13.0], 3.0) == (9.0, 12.0)  # no segment holds it: equally near, so the longer one
    assert choose_window([(2.0, 8.0)], [], 2.0) == (4.0, 6.0)
    assert choose_window([], [1.0], 3.0) is None


def test_output_fps_keeps_every_recorded_frame():
    assert output_fps_for(240, 0.25, None) == 60
    assert output_fps_for(120, 0.25, None) == 30
    assert output_fps_for(60, 0.25, None) == 24
    assert output_fps_for(240, 0.25, 30) == 30


def test_slow_args_cut_stretch_and_drop_audio():
    args = slow_args((2.0, 5.5), 0.25, 60)
    assert args[args.index("-ss") + 1] == "2.000" and args[args.index("-t") + 1] == "3.500"
    assert "-an" in args and args[args.index("-vf") + 1] == "setpts=PTS/0.25,fps=60"


def test_plan_takes_trims_the_recording_and_skips_unseen_plays():
    A, B = "76561198000000001", "76561198000000002"
    demo = make_demo([make_kill(1000 + 640, round_index=0, attacker=A, victim=B)], [make_round(0, 0, 100000)])
    moment = Moment(steamid=A, name="FuzzyHat", side="CT", round_index=0, kills=list(demo.kills), score=5.0)

    def seq(number, **extra):
        return Sequence(number, 1000, 1000 + 64 * 20, [CameraAction(1000, A)], f"s{number}", 5.0, 0, [moment], **extra)

    pov = seq(1)
    take = seq(2, campath=Path("p.campath"), cinematic_of=1, splice_segments=[(5.0, 14.0)])
    clip = SimpleNamespace(name="semi-fuzzy-1", pov=pov, group=[pov, take])
    unseen = SimpleNamespace(name="semi-fuzzy-2", pov=pov, group=[pov])
    takes = plan_takes([clip, unseen], demo, 3.0, "semi")
    assert len(takes) == 1
    t = takes[0]
    assert t.window == (8.5, 11.5) and t.kills == [10.0] and t.sequence.number == 1
    assert t.sequence.end_tick == 1000 + int(11.75 * demo.tickrate)  # window end + the tail
    report = take_report(t, 0.25, 240, 60, 12.0)
    assert report["kill_times_seconds"] == [6.0] and report["kind"] == "teaser" and report["audio"] == "none"
