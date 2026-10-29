"""CLI entry point.

`highlights match.dem -o out.mp4` is the main flow (the `run` subcommand is
implied). `highlights doctor` validates the environment.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import click

from highlights import __version__
from highlights.analysis.clutch import attach_clutches, detect_clutches
from highlights.analysis.moments import cluster_moments
from highlights.analysis.scoring import score_moments
from highlights.analysis.selection import select_sequences
from highlights.config import Config, load_config
from highlights.demo.parser import DemoData, ensure_dem_file, parse_demo
from highlights.demo.players import PlayerNotFound, resolve_players
from highlights.models import CameraAction, Sequence
from highlights.report import print_sequences_table, write_report
from highlights.util.log import console, error, info, warn


class DefaultRunGroup(click.Group):
    """Treat `highlights match.dem` as `highlights run match.dem`."""

    def resolve_command(self, ctx, args):
        try:
            return super().resolve_command(ctx, args)
        except click.UsageError:
            command = self.get_command(ctx, "run")
            return "run", command, args


@click.group(cls=DefaultRunGroup)
@click.version_option(__version__)
def main() -> None:
    """Generate a highlights MP4 from a CS2 demo."""


def _resolve_work_dir(config: Config, demo_path: Path) -> Path:
    if config.paths.work_dir != "auto":
        return Path(config.paths.work_dir) / demo_path.stem
    # Not %TEMP%: Store Python virtualizes Temp/AppData writes (see config.py).
    from highlights.config import app_data_dir

    return app_data_dir() / "work" / demo_path.stem


def _analyze(demo: DemoData, config: Config, queries: list[str]) -> list[Sequence]:
    moments = cluster_moments(demo.kills, demo.tickrate, config.selection.kill_gap_seconds)
    clutches = detect_clutches(demo)
    moments = attach_clutches(moments, clutches, demo)
    score_moments(moments, demo)

    player_steamids: list[str] | None = None
    if queries:
        player_steamids = resolve_players(demo, queries)
        names = ", ".join(f"{demo.player_names[sid]} ({sid})" for sid in player_steamids)
        info(f"Filtering highlights to players: {names}")

    return select_sequences(moments, demo, config.selection, config.recording, player_steamids)


@main.command()
@click.argument("demo", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("-o", "--output", type=click.Path(path_type=Path), default=None, help="Output MP4 path")
@click.option("--player", default=None, help="Only this player's highlights (steamid64 or name)")
@click.option(
    "--players",
    "players",
    multiple=True,
    help="Only these players' highlights: comma-separated and/or repeated (steamid64 or name)",
)
@click.option("--top", type=int, default=None, help="Max number of highlights")
@click.option("--max-duration", type=float, default=None, help="Max total video length, seconds")
@click.option("--min-kills", type=int, default=None, help="Min kills for a moment to qualify")
@click.option("--fps", type=int, default=None, help="Recording framerate")
@click.option("--width", type=int, default=None, help="Recording width")
@click.option("--height", type=int, default=None, help="Recording height")
@click.option("--crf", type=int, default=None, help="x264 CRF for recorded clips")
@click.option("--json-only", is_flag=True, help="Analyze only: write highlights.json and stop")
@click.option("--keep-clips", is_flag=True, help="Keep the work dir with individual clips")
@click.option("--overlays/--no-overlays", default=False, help="Draw a text label on each clip")
@click.option(
    "--transitions",
    type=click.Choice(["none", "fade"]),
    default="none",
    help="Transition between clips (fade re-encodes, slower)",
)
def run(
    demo: Path,
    output: Path | None,
    player: str | None,
    players: tuple[str, ...],
    top: int | None,
    max_duration: float | None,
    min_kills: int | None,
    fps: int | None,
    width: int | None,
    height: int | None,
    crf: int | None,
    json_only: bool,
    keep_clips: bool,
    overlays: bool,
    transitions: str,
) -> None:
    """Parse DEMO, detect highlights, record them in-game and produce an MP4."""
    config = load_config()
    for section, name, value in (
        (config.selection, "top", top),
        (config.selection, "max_duration", max_duration),
        (config.selection, "min_kills", min_kills),
        (config.recording, "fps", fps),
        (config.recording, "width", width),
        (config.recording, "height", height),
        (config.recording, "crf", crf),
    ):
        if value is not None:
            setattr(section, name, value)

    output = output or demo.with_name(demo.stem + "-highlights.mp4")
    work_dir = _resolve_work_dir(config, demo)
    work_dir.mkdir(parents=True, exist_ok=True)

    with console.status("Parsing demo..."):
        demo_path = ensure_dem_file(demo, work_dir)
        parsed = parse_demo(demo_path)
    info(
        f"Parsed [bold]{parsed.map_name}[/bold]: {len(parsed.rounds)} rounds, "
        f"{len(parsed.kills)} kills, tickrate {parsed.tickrate:.0f}"
    )

    try:
        queries = [*([player] if player else []), *players]
        sequences = _analyze(parsed, config, queries)
    except PlayerNotFound as exc:
        raise click.ClickException(str(exc))

    if not sequences:
        raise click.ClickException(
            "No highlights matched the criteria — try --min-kills 1 or check the demo"
        )

    print_sequences_table(parsed, sequences)
    report_path = output.with_suffix(".json")
    write_report(report_path, parsed, sequences)
    info(f"Report written to {report_path}")

    if json_only:
        return

    total = sum(s.duration_seconds(parsed.tickrate) for s in sequences)
    info(f"Estimated recording time: at least {int(total + len(sequences) * 20 + 90)}s (runs the game)")

    from highlights.recording.recorder import RecordingSetupError, record, resolve_tools
    from highlights.video.concat import concat_clips, concat_clips_with_fade
    from highlights.video.postprocess import process_clip

    started = time.monotonic()
    try:
        outputs = record(parsed, sequences, config, work_dir)
    except RecordingSetupError as exc:
        raise click.ClickException(str(exc))
    if not outputs:
        raise click.ClickException("Recording produced no clips — see warnings above")

    tools = resolve_tools(config, auto_install=False)
    with console.status("Post-processing clips..."):
        clips = [process_clip(tools.ffmpeg_exe, o, work_dir / "clips", overlays) for o in outputs]
        if transitions == "fade":
            concat_clips_with_fade(tools.ffmpeg_exe, clips, output)
        else:
            concat_clips(tools.ffmpeg_exe, clips, output, work_dir)

    if not keep_clips:
        shutil.rmtree(work_dir, ignore_errors=True)
    else:
        info(f"Individual clips kept in {work_dir}")

    elapsed = int(time.monotonic() - started)
    console.print(
        f"[green]Done:[/green] {output} "
        f"({len(clips)} clips, {total:.0f}s of highlights, took {elapsed}s)"
    )


@main.command()
def doctor() -> None:
    """Check that everything needed for recording is in place."""
    from rich.table import Table

    from highlights.recording import steam, toolchain

    config = load_config()
    table = Table(title="cs2-highlights doctor")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Detail")

    def row(name: str, ok: bool | None, detail: str) -> None:
        status = "[green]OK[/green]" if ok else ("[yellow]--[/yellow]" if ok is None else "[red]FAIL[/red]")
        table.add_row(name, status, detail)

    steam_folder = steam.get_steam_folder()
    row("Steam installed", steam_folder is not None, str(steam_folder or "registry key not found"))

    cs2_folder = steam.get_cs2_folder()
    if config.paths.cs2_exe != "auto":
        cs2_exe = Path(config.paths.cs2_exe)
    else:
        cs2_exe = steam.get_cs2_exe(cs2_folder) if cs2_folder else None
    row(
        "CS2 installed",
        cs2_exe is not None and cs2_exe.is_file(),
        str(cs2_exe or "app 730 not found in Steam library"),
    )

    row(
        "Steam running",
        steam.is_steam_running(),
        "required at recording time" if not steam.is_steam_running() else "",
    )

    hlae = toolchain.find_hlae(config)
    row("HLAE", True if hlae else None, str(hlae or "missing — will be auto-downloaded on first run"))

    if hlae and cs2_folder:
        from datetime import datetime, timezone

        inf = steam.read_steam_inf(cs2_folder)
        hlae_info = toolchain.read_hlae_install_info(hlae)
        cs2_date = hlae_date = None
        try:
            cs2_date = datetime.strptime(inf.get("VersionDate", ""), "%b %d %Y").replace(tzinfo=timezone.utc)
            hlae_date = datetime.fromisoformat(hlae_info.get("published_at", "").replace("Z", "+00:00"))
        except ValueError:
            pass
        if cs2_date and hlae_date:
            compatible = hlae_date >= cs2_date
            row(
                "HLAE vs CS2 build",
                True if compatible else False,
                f"CS2 {inf.get('PatchVersion', '?')} from {cs2_date:%Y-%m-%d}, "
                f"HLAE {hlae_info.get('tag', '?')} from {hlae_date:%Y-%m-%d}"
                + ("" if compatible else " — CS2 is newer, injection will likely crash; run `highlights update-tools` once advancedfx releases an update"),
            )

    ffmpeg = toolchain.find_ffmpeg(config)
    row("FFmpeg", True if ffmpeg else None, str(ffmpeg or "missing — will be auto-downloaded on first run"))

    plugin_dll = toolchain.find_plugin_dll(config)
    row(
        "CS2 server plugin",
        plugin_dll is not None,
        str(plugin_dll or "install CS Demo Manager or put server.dll in vendor/cs2-plugin/"),
    )

    from highlights.config import app_data_dir

    work_base = app_data_dir()
    work_base.mkdir(parents=True, exist_ok=True)
    free_gb = shutil.disk_usage(work_base).free / (1 << 30)
    row("Disk space (work dir)", free_gb > 5, f"{free_gb:.1f} GB free at {work_base}")

    console.print(table)


@main.command(name="update-tools")
def update_tools() -> None:
    """Re-download the latest HLAE and FFmpeg (e.g. after a CS2 update broke HLAE)."""
    from highlights.recording import toolchain

    hlae = toolchain.install_hlae()
    ffmpeg = toolchain.install_ffmpeg()
    toolchain.write_ffmpeg_ini(hlae, ffmpeg)
    console.print("[green]Tools updated.[/green] Run `highlights doctor` to verify.")


@main.command(hidden=True, name="record-one")
@click.argument("demo", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--start-tick", type=int, required=True)
@click.option("--end-tick", type=int, required=True)
@click.option("--steamid", required=True, help="Player to spectate (steamid64)")
@click.option("-o", "--output", type=click.Path(path_type=Path), default=None)
def record_one(demo: Path, start_tick: int, end_tick: int, steamid: str, output: Path | None) -> None:
    """Recording proof of concept: record a single arbitrary clip from a demo."""
    from highlights.recording.recorder import record, resolve_tools
    from highlights.video.postprocess import process_clip

    config = load_config()
    work_dir = _resolve_work_dir(config, demo)
    work_dir.mkdir(parents=True, exist_ok=True)

    with console.status("Parsing demo..."):
        demo_path = ensure_dem_file(demo, work_dir)
        parsed = parse_demo(demo_path)

    if steamid not in parsed.slots:
        warn(f"steamid {steamid} has no known slot; known: {parsed.slots}")

    sequence = Sequence(
        number=1,
        start_tick=start_tick,
        end_tick=end_tick,
        cameras=[CameraAction(tick=start_tick, steamid=steamid)],
        label=f"PoC clip {start_tick}-{end_tick}",
        score=0.0,
        round_index=0,
    )
    outputs = record(parsed, [sequence], config, work_dir)
    if not outputs:
        raise click.ClickException("No clip was recorded")

    tools = resolve_tools(config, auto_install=False)
    clip = process_clip(tools.ffmpeg_exe, outputs[0], work_dir / "clips", overlay=False)
    final = output or demo.with_name(demo.stem + "-poc.mp4")
    shutil.copyfile(clip, final)
    console.print(f"[green]PoC clip:[/green] {final}")


if __name__ == "__main__":
    main()
