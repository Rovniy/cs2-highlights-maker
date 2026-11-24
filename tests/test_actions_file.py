import json

from conftest import make_demo, make_kill, make_round

from highlights.config import RecordingConfig
from highlights.models import CameraAction, Sequence
from highlights.recording.actions_file import ActionsFile
from highlights.recording.sequences import build_actions_file


def _sequence(number: int, start: int, end: int, steamid: str = "111") -> Sequence:
    return Sequence(
        number=number,
        start_tick=start,
        end_tick=end,
        cameras=[CameraAction(tick=start, steamid=steamid)],
        label="test",
        score=1.0,
        round_index=0,
    )


def test_actions_file_format(tmp_path):
    demo_path = tmp_path / "match.dem"
    actions = ActionsFile(demo_path)
    actions.add_exec(1, "sv_cheats 1")
    actions.add_go_to_next_sequence(5000)
    actions.add_exec(1, "sv_cheats 1")
    path = actions.write()

    assert path == tmp_path / "match.dem.json"
    data = json.loads(path.read_text())
    assert isinstance(data, list)
    assert len(data) == 2
    assert all("actions" in group for group in data)
    first = data[0]["actions"][0]
    assert set(first) == {"tick", "cmd"}


def test_ticks_clamped_to_96(tmp_path):
    actions = ActionsFile(tmp_path / "match.dem")
    actions.add_exec(1, "sv_cheats 1")
    actions.add_go_to_tick(1, 10)
    path = actions.write()
    data = json.loads(path.read_text())
    ticks = [a["tick"] for a in data[0]["actions"]]
    assert all(t >= 96 for t in ticks)
    assert data[0]["actions"][1]["cmd"] == "demo_gototick 96"


def test_build_actions_two_sequences(tmp_path):
    demo_path = tmp_path / "match.dem"
    rounds = [make_round(0, 0, 100000)]
    kills = [make_kill(5000, attacker="111")]
    demo = make_demo(kills, rounds, slots={"111": 3})

    sequences = [_sequence(1, 5000, 6000), _sequence(2, 20000, 21000)]
    actions = build_actions_file(demo_path, sequences, demo, RecordingConfig(), tmp_path / "work")
    path = actions.write()
    data = json.loads(path.read_text())

    assert len(data) == 2
    cmds_1 = [a["cmd"] for a in data[0]["actions"]]
    cmds_2 = [a["cmd"] for a in data[1]["actions"]]

    assert "mirv_streams record start" in cmds_1
    assert "mirv_streams record end" in cmds_1
    assert "pause_playback" in cmds_1
    assert "go_to_next_sequence" in cmds_1
    assert any(c.startswith("demo_gototick") for c in cmds_1)
    assert "spec_mode 1" in cmds_1
    assert "spec_player 3" in cmds_1
    # Last sequence quits the game instead of advancing.
    assert "quit" in cmds_2
    assert "go_to_next_sequence" not in cmds_2


def test_ffmpeg_preset_uses_quote_placeholder_and_unix_paths(tmp_path):
    demo_path = tmp_path / "match.dem"
    rounds = [make_round(0, 0, 100000)]
    demo = make_demo([make_kill(5000, attacker="111")], rounds, slots={"111": 3})
    work_dir = tmp_path / "out dir"

    actions = build_actions_file(demo_path, [_sequence(1, 5000, 6000)], demo, RecordingConfig(), work_dir)
    path = actions.write()
    content = path.read_text()
    data = json.loads(content)
    cmds = [a["cmd"] for a in data[0]["actions"]]

    preset_cmd = next(c for c in cmds if c.startswith("mirv_streams settings add ffmpeg"))
    assert "{QUOTE}" in preset_cmd
    assert "\\\\video.mp4" in preset_cmd
    name_cmd = next(c for c in cmds if c.startswith("mirv_streams record name"))
    assert "\\" not in name_cmd  # unix separators only
    assert "1-sequence" in name_cmd


def test_missing_slot_raises(tmp_path):
    demo_path = tmp_path / "match.dem"
    rounds = [make_round(0, 0, 100000)]
    demo = make_demo([make_kill(5000, attacker="111")], rounds, slots={})

    try:
        build_actions_file(demo_path, [_sequence(1, 5000, 6000)], demo, RecordingConfig(), tmp_path)
        raised = False
    except ValueError:
        raised = True
    assert raised


def test_record_window_matches_sequence_ticks(tmp_path):
    demo_path = tmp_path / "match.dem"
    rounds = [make_round(0, 0, 100000)]
    demo = make_demo([make_kill(5000, attacker="111")], rounds, slots={"111": 3})

    actions = build_actions_file(demo_path, [_sequence(1, 5000, 6000)], demo, RecordingConfig(), tmp_path)
    data = json.loads(actions.write().read_text())
    by_cmd = {a["cmd"]: a["tick"] for a in data[0]["actions"]}
    assert by_cmd["mirv_streams record start"] == 5000
    assert by_cmd["mirv_streams record end"] == 6000
    assert by_cmd["pause_playback"] == 5000 - 4
    assert by_cmd["quit"] == 6000 + 64


def test_name_overrides_emitted_once_per_sequence(tmp_path):
    rounds = [make_round(0, 0, 100000)]
    demo = make_demo([make_kill(5000, attacker="111")], rounds, slots={"111": 3})
    recording = RecordingConfig(player_names={"111": 'Cool "Guy"', "abc": "bad id", "222": " "})

    sequences = [_sequence(1, 5000, 6000), _sequence(2, 20000, 21000)]
    actions = build_actions_file(tmp_path / "match.dem", sequences, demo, recording, tmp_path)
    data = json.loads(actions.write().read_text())

    for group in data:
        cmds = [a["cmd"] for a in group["actions"] if a["cmd"].startswith("mirv_replace_name")]
        assert cmds == ['mirv_replace_name byXuid add x111 "Cool Guy"']


def test_no_name_overrides_by_default(tmp_path):
    rounds = [make_round(0, 0, 100000)]
    demo = make_demo([make_kill(5000, attacker="111")], rounds, slots={"111": 3})
    actions = build_actions_file(tmp_path / "match.dem", [_sequence(1, 5000, 6000)], demo, RecordingConfig(), tmp_path)
    data = json.loads(actions.write().read_text())
    assert not any("mirv_replace_name" in a["cmd"] for a in data[0]["actions"])


def test_player_names_loaded_from_toml(tmp_path):
    from highlights.config import load_config

    path = tmp_path / "config.toml"
    path.write_text('[recording.player_names]\n76561198000000000 = "Ace"\n')
    assert load_config(path).recording.player_names == {"76561198000000000": "Ace"}
