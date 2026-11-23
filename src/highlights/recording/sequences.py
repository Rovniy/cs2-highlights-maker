"""Build the per-sequence command plan (port of CSDM's create-cs2-video-json-file.ts).

Recording system: HLAE mirv_streams piping frames straight into FFmpeg — no
raw TGA files. Audio comes from `mirv_streams record startMovieWav 1` which
writes take****/audio.wav inside the sequence output folder.

LANChamp capture defaults:
- Game audio enabled, CS2 music muted.
- Only the active POV player's kills appear in the kill feed.
- POV kill notices persist to the end of the clip instead of fading after 5 seconds.
- One standardized crosshair for all observed players.
"""

from __future__ import annotations

import math
from pathlib import Path

from highlights.config import RecordingConfig
from highlights.demo.parser import DemoData
from highlights.models import Sequence
from highlights.recording.actions_file import ActionsFile
from highlights.util.log import warn

# Audio: keep weapon sounds, footsteps, grenades, etc.; remove CS2's music.
AUDIO_COMMANDS = (
    "volume 1",
    "snd_musicvolume 0",
    "snd_menumusic_volume 0",
    "snd_roundstart_volume 0",
    "snd_roundaction_volume 0",
    "snd_roundend_volume 0",
    "snd_mvp_volume 0",
    "snd_mapobjective_volume 0",
    "snd_tensecondwarning_volume 0",
    "snd_deathcamera_volume 0",
    # Don't lose game sound if CS2 isn't the foreground window while recording.
    "snd_mute_losefocus 0",
)

# CS2's September 2026 crosshair update renamed several console variables.
# This is a consistent, static green/cyan crosshair at 1080p.
CROSSHAIR_COMMANDS = (
    "crosshair 1",
    "cl_show_observer_crosshair 0",
    "cl_crosshairstyle 4",
    "cl_crosshair_length 4",
    "cl_crosshair_thickness 0.5",
    "cl_crosshair_gap 3",
    "cl_crosshairdot 0",
    "cl_crosshair_t 0",
    "cl_crosshair_drawoutline 1",
    "cl_crosshaircolor_r 0",
    "cl_crosshaircolor_g 255",
    "cl_crosshaircolor_b 0",
    "cl_crosshaircolor_a 255",
    "cl_crosshair_recoil 0",
    "cl_crosshairsize 2.5"
)

# Hide telemetry/debug HUD elements that would end up in the recording.
MANDATORY_COMMANDS = (
    "sv_cheats 1",
    *AUDIO_COMMANDS,
    *CROSSHAIR_COMMANDS,
    "cl_hud_telemetry_frametime_show 0",
    "cl_hud_telemetry_net_misdelivery_show 0",
    "cl_hud_telemetry_ping_show 0",
    "cl_hud_telemetry_serverrecvmargin_graph_show 0",
    "cl_trueview_show_status 0",
    "r_show_build_info 0",
    "mirv_streams record screen enabled 1",
    "cl_demo_predict 0",
)


def to_unix_path(path: Path | str) -> str:
    return str(path).replace("\\", "/")


def name_override_commands(player_names: dict[str, str]) -> list[str]:
    """`mirv_replace_name` commands for a SteamID64 -> display name map.

    XUID equals the SteamID64 in CS2 and, unlike the user id, is stable across
    demos. Entries with a non-numeric id or a name that is empty after cleaning
    are skipped; quotes and control characters would break the console command.
    """
    commands = []
    for steamid, name in player_names.items():
        steamid = str(steamid).strip()
        clean = "".join(c for c in str(name) if c.isprintable() and c not in '"\\;').strip()
        if steamid.isdigit() and clean:
            commands.append(f'mirv_replace_name byXuid add x{steamid} "{clean}"')
    return commands


def hex_to_rgba(value: str) -> str | None:
    """"#RRGGBB" / "#RRGGBBAA" -> "R G B A" (alpha defaults to 255); None when not a hex color."""
    text = value.strip().removeprefix("#")
    if len(text) not in (6, 8) or any(c not in "0123456789abcdefABCDEF" for c in text):
        return None
    channels = [int(text[i : i + 2], 16) for i in range(0, len(text), 2)]
    if len(channels) == 3:
        channels.append(255)
    return " ".join(str(c) for c in channels)


def parse_color(value: str) -> str | None:
    """"#RRGGBB[AA]" or "R G B A" (0-255 each, alpha optional) -> "R G B A"; None when invalid."""
    parts = value.replace(",", " ").split()
    if len(parts) in (3, 4) and all(p.isdigit() and int(p) <= 255 for p in parts):
        return " ".join([*parts, "255"][:4])
    return hex_to_rgba(value) if len(parts) == 1 else None


def extra_console_commands(commands: object) -> list[str]:
    """The user's own console commands, one string each; blanks are dropped, a non-list is ignored."""
    if not isinstance(commands, (list, tuple)):
        return []
    return [text for text in (str(c).strip() for c in commands) if text]


def killfeed_color_commands(recording: RecordingConfig) -> list[str]:
    """`mirv_deathmsg colors <target> R G B A` for each configured color; invalid values are warned about and skipped.

    background, backgroundLocal and border default to fully transparent while `killfeed_plain` is on.
    """
    plain = "0 0 0 0" if recording.killfeed_plain else ""
    targets = (
        ("ct", recording.killfeed_ct_color, ""),
        ("t", recording.killfeed_t_color, ""),
        ("background", recording.killfeed_background_color, plain),
        ("backgroundLocal", recording.killfeed_background_local_color, plain),
        ("border", recording.killfeed_border_color, plain),
    )
    commands = []
    for target, value, default in targets:
        if value.strip():
            rgba = parse_color(value)
            if rgba is None:
                warn(f"Ignoring killfeed color for {target} {value!r}: expected #RRGGBB, #RRGGBBAA or 'R G B A'")
                continue
        else:
            rgba = default
        if rgba:
            commands.append(f"mirv_deathmsg colors {target} {rgba}")
    return commands


def _apply_pov_killfeed_filter(
    actions: ActionsFile, tick: int, steamid: str
) -> None:
    """Allow only kills by this POV player (no highlight: the feed holds nothing else).

    `attackerMatch=!x... block=1 lastRule=1` is documented by Advancedfx
    for excluding every death notice whose attacker is not this SteamID.
    """
    actions.add_exec(tick, "mirv_deathmsg filter clear")
    actions.add_exec(
        tick,
        f"mirv_deathmsg filter add attackerMatch=!x{steamid} block=1 lastRule=1",
    )


def _apply_killfeed(actions: ActionsFile, tick: int, sequence: Sequence, steamid: str) -> None:
    """POV clips show only the POV player's kills; cinematic clips show none."""
    if sequence.campath is None:
        _apply_pov_killfeed_filter(actions, tick, steamid)
        return
    # Block every death notice: attackerMatch=!x0 matches any attacker that is not
    # xuid 0 (same documented filter syntax as the POV rule above). Bots have
    # xuid 0, so their kills would still show.
    actions.add_exec(tick, "mirv_deathmsg filter clear")
    actions.add_exec(tick, "mirv_deathmsg filter add attackerMatch=!x0 block=1 lastRule=1")


def build_actions_file(
    demo_path: Path,
    sequences: list[Sequence],
    demo: DemoData,
    recording: RecordingConfig,
    work_dir: Path,
) -> ActionsFile:
    actions = ActionsFile(demo_path)
    tickrate = round(demo.tickrate)
    # Only touch campath state when a cinematic sequence exists, so plain POV
    # runs emit exactly the commands they always did.
    uses_campaths = any(s.campath for s in sequences)
    name_overrides = name_override_commands(recording.player_names)
    killfeed_colors = killfeed_color_commands(recording)
    extra_commands = extra_console_commands(recording.extra_commands)

    for i, sequence in enumerate(sequences):
        if not sequence.cameras:
            raise ValueError(f"{sequence.name} has no POV camera actions")

        # HLAE expresses death-notice lifetime in seconds. Keep notices alive
        # through the full clip (plus a safety margin), including the first
        # kill in a 4K/ACE. At least 60 seconds avoids short-notice expiry.
        killfeed_lifetime = max(
            60,
            math.ceil(sequence.duration_seconds(demo.tickrate)) + 10,
        )

        for command in MANDATORY_COMMANDS:
            actions.add_exec(1, command)
        actions.add_exec(1, f"cl_draw_only_deathnotices {1 if recording.clean_hud else 0}")
        actions.add_exec(1, f"mirv_deathmsg lifetime {killfeed_lifetime}")
        # Keep the lifetime multiplier at 1.0 so no notice expires early.
        actions.add_exec(1, "mirv_deathmsg lifetimeMod 1.0")
        actions.add_exec(1, "mirv_deathmsg filter clear")

        setup_tick = max(1, sequence.start_tick - tickrate)
        output_folder = f"{to_unix_path(work_dir)}/{sequence.name}"
        preset = f"preset{sequence.number}"

        actions.add_exec(setup_tick, "mirv_streams record startMovieWav 1")
        actions.add_exec(setup_tick, f'mirv_streams record name "{output_folder}"')
        # Reinforce the duration at setup, after seeking from the start.
        actions.add_exec(setup_tick, f"mirv_deathmsg lifetime {killfeed_lifetime}")
        actions.add_exec(setup_tick, "mirv_deathmsg lifetimeMod 1.0")
        # Reset stale kills from the preceding sequence.
        actions.add_exec(setup_tick, "mirv_deathmsg clear")
        for command in (*name_overrides, *killfeed_colors, *extra_commands):
            actions.add_exec(setup_tick, command)
        actions.add_exec(setup_tick, f"spec_show_xray {1 if sequence.xray else 0}")
        actions.add_exec(setup_tick, "mp_display_kill_assists 1")
        if uses_campaths:
            # A previous cinematic sequence must not leak its path into this one.
            actions.add_exec(setup_tick, "mirv_campath enabled 0")
            actions.add_exec(setup_tick, "mirv_campath clear")
        _apply_killfeed(actions, setup_tick, sequence, sequence.cameras[0].steamid)

        # {QUOTE} is HLAE's escaped-quote placeholder; the literal "\\" before
        # video.mp4 is unescaped to "\" by HLAE (as CSDM generates it).
        preset_args = f"-c:v libx264 -pix_fmt yuv420p -crf {recording.crf}"
        actions.add_exec(
            setup_tick,
            f'mirv_streams settings add ffmpeg {preset} "{preset_args} '
            f"{{QUOTE}}{output_folder}\\\\video.mp4{{QUOTE}}\"",
        )
        actions.add_exec(setup_tick, f"mirv_streams record screen settings {preset}")
        actions.add_exec(setup_tick, f"mirv_streams record fps {recording.fps}")

        # Pause briefly before start so the post-seek loading tint has time
        # to fade (the plugin pauses, sleeps 2s wall-clock, resumes).
        actions.add_pause_playback(max(1, sequence.start_tick - 4))

        # Seek to one tick before setup so setup commands run. spec_player on
        # the same tick as demo_gototick may be ignored (CSDM issue #1238).
        actions.add_go_to_tick(1, max(1, setup_tick - 1))

        missing_slots: list[str] = []
        for camera in sequence.cameras:
            slot = demo.slots.get(camera.steamid)
            if slot is None:
                missing_slots.append(camera.steamid)
                continue

            actions.add_spec_player(camera.tick, slot)
            # On POV changes, reset the feed so it only contains kills by
            # the newly spectated player. Kills persist until that switch.
            actions.add_exec(camera.tick, "mirv_deathmsg clear")
            _apply_killfeed(actions, camera.tick, sequence, camera.steamid)

        if missing_slots:
            raise ValueError(
                f"No spec_player slot for steamid(s) {missing_slots} — cannot aim the camera"
            )

        if sequence.campath is not None:
            # `edit start` with no argument rebases the path to start at the
            # current demo time (this is how observer-pack binds use it), so the
            # path plays from the first recorded frame.
            actions.add_exec(sequence.start_tick, "mirv_campath enabled 1")
            campath_file = sequence.campath
            if sequence.campath_retime:
                from highlights.recording.campaths import parse_campath, write_retimed

                original = parse_campath(sequence.campath)
                if original is None:
                    raise ValueError(f"Cam path {sequence.campath} cannot be read to retime it")
                campath_file = write_retimed(
                    original,
                    sequence.campath_retime["offset_seconds"],
                    sequence.campath_retime["hold_until_seconds"],
                    work_dir / "campaths" / f"{sequence.campath.stem}_{sequence.start_tick}.campath",
                )
            actions.add_exec(sequence.start_tick, f'mirv_campath load "{to_unix_path(campath_file)}"')
            actions.add_exec(sequence.start_tick, "mirv_campath edit start")

        actions.add_exec(sequence.start_tick, "mirv_streams record start")
        actions.add_exec(sequence.end_tick, "mirv_streams record end")
        if sequence.campath is not None:
            actions.add_exec(sequence.end_tick, "mirv_campath enabled 0")

        if i == len(sequences) - 1:
            actions.add_exec(sequence.end_tick + 64, "quit")
        else:
            actions.add_go_to_next_sequence(sequence.end_tick + 64)

    return actions
