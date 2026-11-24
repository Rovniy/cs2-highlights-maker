import json

from highlights.report import finalize_report


def _kill(seconds, pov=True, headshot=False):
    return {
        "type": "kill",
        "seconds_from_clip_start": seconds,
        "tick": int(seconds * 64),
        "weapon": "ak47",
        "headshot": headshot,
        "noscope": False,
        "thrusmoke": False,
        "attackerblind": False,
        "penetrated": 0,
        "victim": {"name": "v"},
        "is_pov_kill": pov,
        "is_selected_moment_kill": pov,
    }


def _report(tmp_path):
    path = tmp_path / "out.json"
    data = {
        "editing": {},
        "sequences": [
            {"number": 1, "events": [_kill(4.0, headshot=True), _kill(10.0, pov=False)]},
            {"number": 2, "events": [_kill(2.0)]},
            {"number": 3, "events": [_kill(1.0)]},  # not recorded
        ],
    }
    path.write_text(json.dumps(data))
    return path


def test_video_times_accumulate_clip_durations(tmp_path):
    path = _report(tmp_path)
    finalize_report(path, {1: 20.0, 2: 15.0})
    data = json.loads(path.read_text())
    assert [t["video_time_seconds"] for t in data["beat_targets"]] == [4.0, 22.0]
    assert data["sequences"][1]["video_start_seconds"] == 20.0
    assert "video_start_seconds" not in data["sequences"][2]
    assert data["beat_targets"][0]["headshot"] is True


def test_non_pov_kill_gets_time_but_is_no_beat_target(tmp_path):
    path = _report(tmp_path)
    finalize_report(path, {1: 20.0, 2: 15.0})
    data = json.loads(path.read_text())
    assert data["sequences"][0]["events"][1]["video_time_seconds"] == 10.0
    assert len(data["beat_targets"]) == 2


def test_crossfade_overlap_and_offset(tmp_path):
    path = _report(tmp_path)
    finalize_report(path, {1: 20.0, 2: 15.0}, overlap_seconds=0.5, offset_seconds=0.1)
    targets = json.loads(path.read_text())["beat_targets"]
    assert [t["video_time_seconds"] for t in targets] == [4.1, 21.6]
