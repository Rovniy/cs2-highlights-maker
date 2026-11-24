from conftest import make_demo, make_kill, make_round

from highlights.models import Moment


def test_checklist_lists_what_to_look_for():
    from highlights.analysis.selection import build_sequences
    from highlights.config import Config
    from highlights.ingame_check import CheckPlan, checklist

    kills = [make_kill(5000), make_kill(5200)]
    demo = make_demo(kills, [make_round(0, 0, 20000)])
    moment = Moment("111", "player111", "CT", 0, kills)
    config = Config()
    config.recording.player_names = {"222": "Victim"}
    config.recording.extra_commands = ["r_dof_override 1"]
    [pov] = build_sequences([moment], demo, config.selection, config.recording, merge=False)
    text = checklist(demo, config, CheckPlan(moment, [pov], pov, notes=["No cam paths"]), 0.12)
    assert "01-pov.mp4" in text and "player111" in text and "+0.12 s" in text
    assert "player222 → Victim" in text and "r_dof_override 1" in text
    assert "02-campath" not in text and "- No cam paths" in text


def test_checklist_asks_whether_the_xray_cvar_shows():
    from dataclasses import replace

    from highlights.analysis.selection import build_sequences
    from highlights.config import Config
    from highlights.ingame_check import CheckPlan, checklist

    kills = [make_kill(5000)]
    demo = make_demo(kills, [make_round(0, 0, 20000)])
    moment = Moment("111", "player111", "CT", 0, kills)
    config = Config()
    [pov] = build_sequences([moment], demo, config.selection, config.recording, merge=False)
    window = {"start_seconds": 4.0, "end_seconds": 5.5, "fade_in": 0.4, "fade_out": 0.4}
    take = replace(pov, number=2, cinematic_of=1, splice_segments=[(4.0, 5.5)], xray={"fade_seconds": 0.4, "windows": [window]})
    text = checklist(demo, config, CheckPlan(moment, [pov, take], pov, xray=take), None)
    assert "05-xray.mp4" in text and "spec_show_xray 1" in text and "4.0–5.5" in text
    assert "04-spliced.mp4" in text
    assert "05-xray" not in checklist(demo, config, CheckPlan(moment, [pov], pov), None)
