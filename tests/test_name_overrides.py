from highlights.recording.sequences import name_override_commands


def test_empty_dict_returns_empty_list():
    """No players means no commands."""
    assert name_override_commands({}) == []


def test_single_entry():
    """A normal SteamID64 -> name pair produces one command."""
    assert name_override_commands({"76561198000000001": "Ace"}) == [
        'mirv_replace_name byXuid add x76561198000000001 "Ace"'
    ]


def test_names_with_spaces_are_kept_and_quoted():
    """Spaces survive cleaning and the name stays wrapped in quotes."""
    assert name_override_commands({"76561198000000002": "Sir Nice Guise"}) == [
        'mirv_replace_name byXuid add x76561198000000002 "Sir Nice Guise"'
    ]


def test_quotes_backslashes_and_semicolons_are_stripped():
    """Console-breaking characters are removed from the name."""
    assert name_override_commands({"76561198000000003": 'A"b\\c;d'}) == [
        'mirv_replace_name byXuid add x76561198000000003 "Abcd"'
    ]


def test_non_numeric_steamid_is_skipped():
    """An id that is not all digits produces no command."""
    assert name_override_commands({"BOT01": "Ace"}) == []


def test_name_empty_after_cleaning_is_skipped():
    """A name that cleans down to nothing produces no command."""
    assert name_override_commands({"76561198000000004": ' " \\ ;  '}) == []
    assert name_override_commands({"76561198000000005": "   "}) == []


def test_insertion_order_preserved():
    """Commands follow the dict's insertion order."""
    commands = name_override_commands(
        {
            "76561198000000007": "Third",
            "76561198000000006": "First",
            "BAD": "Skipped",
            "76561198000000005": "Second",
        }
    )
    assert commands == [
        'mirv_replace_name byXuid add x76561198000000007 "Third"',
        'mirv_replace_name byXuid add x76561198000000006 "First"',
        'mirv_replace_name byXuid add x76561198000000005 "Second"',
    ]


# ---- kill-feed colors -------------------------------------------------------


def test_hex_to_rgba():
    from highlights.recording.sequences import hex_to_rgba

    assert hex_to_rgba("#00ACE7") == "0 172 231 255"
    assert hex_to_rgba("f6ca26") == "246 202 38 255"
    assert hex_to_rgba("#F6CA2680") == "246 202 38 128"
    assert hex_to_rgba("#12345") is None and hex_to_rgba("#GGGGGG") is None


def test_killfeed_color_commands_skip_empty_and_invalid():
    from highlights.config import RecordingConfig
    from highlights.recording.sequences import killfeed_color_commands

    plain = [f"mirv_deathmsg colors {p} 0 0 0 0" for p in ("background", "backgroundLocal", "border")]
    assert killfeed_color_commands(RecordingConfig()) == plain
    assert killfeed_color_commands(RecordingConfig(killfeed_plain=False)) == []
    both = RecordingConfig(killfeed_ct_color="#00ACE7", killfeed_t_color="#F6CA26", killfeed_plain=False)
    assert killfeed_color_commands(both) == [
        "mirv_deathmsg colors ct 0 172 231 255",
        "mirv_deathmsg colors t 246 202 38 255",
    ]
    assert killfeed_color_commands(RecordingConfig(killfeed_ct_color="blue", killfeed_plain=False)) == []


def test_actions_file_has_colors_and_no_local_player_highlight(tmp_path):
    from test_campaths import _sequence
    from highlights.config import RecordingConfig
    from highlights.recording.sequences import build_actions_file
    from conftest import make_demo

    demo = make_demo([], [], slots={"111": 1})
    recording = RecordingConfig(killfeed_ct_color="#00ACE7", killfeed_t_color="#F6CA26")
    actions = build_actions_file(tmp_path / "d.dem", [_sequence()], demo, recording, tmp_path)
    text = actions.write().read_text(encoding="utf-8")
    assert "mirv_deathmsg colors ct 0 172 231 255" in text and "mirv_deathmsg colors t 246 202 38 255" in text
    assert "localPlayer" not in text


def test_killfeed_part_colors_override_plain():
    from highlights.config import RecordingConfig
    from highlights.recording.sequences import killfeed_color_commands, parse_color

    assert parse_color("255 0 0") == "255 0 0 255"
    assert parse_color("10, 20, 30, 40") == "10 20 30 40"
    assert parse_color("300 0 0 0") is None
    cfg = RecordingConfig(killfeed_border_color="255 0 0 255", killfeed_background_local_color="#000000AA")
    assert killfeed_color_commands(cfg) == [
        "mirv_deathmsg colors background 0 0 0 0",
        "mirv_deathmsg colors backgroundLocal 0 0 0 170",
        "mirv_deathmsg colors border 255 0 0 255",
    ]
    off = RecordingConfig(killfeed_plain=False, killfeed_border_color="1 2 3 4")
    assert killfeed_color_commands(off) == ["mirv_deathmsg colors border 1 2 3 4"]


def test_extra_commands_are_emitted_at_setup_tick(tmp_path):
    from test_campaths import _sequence
    from highlights.config import RecordingConfig
    from highlights.recording.sequences import build_actions_file, extra_console_commands

    from conftest import make_demo

    assert extra_console_commands(["  a 1 ", "", "b"]) == ["a 1", "b"]
    assert extra_console_commands("not a list") == []
    demo = make_demo([], [], slots={"111": 1})
    recording = RecordingConfig(extra_commands=["my_dof_cmd 1", "my_dof_focus 120"])
    actions = build_actions_file(tmp_path / "d.dem", [_sequence()], demo, recording, tmp_path)
    text = actions.write().read_text(encoding="utf-8")
    assert "my_dof_cmd 1" in text and "my_dof_focus 120" in text
    plain = build_actions_file(tmp_path / "d.dem", [_sequence()], demo, RecordingConfig(), tmp_path)
    assert "my_dof_cmd" not in plain.write().read_text(encoding="utf-8")
