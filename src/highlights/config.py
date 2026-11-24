"""TOML config in ~/.cs2-highlights, merged with defaults and CLI flags.

Deliberately NOT under AppData: Microsoft Store Python virtualizes AppData
writes into its MSIX package sandbox, so files written there by this app are
invisible to the non-packaged processes we orchestrate (HLAE, CS2, FFmpeg).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

APP_NAME = "cs2-highlights"


def app_data_dir() -> Path:
    return Path.home() / f".{APP_NAME}"


def config_file_path() -> Path:
    return app_data_dir() / "config.toml"


@dataclass
class PathsConfig:
    cs2_exe: str = "auto"
    hlae_dir: str = "auto"
    ffmpeg_exe: str = "auto"
    plugin_dll: str = "auto"
    work_dir: str = "auto"
    # One folder or a list (searched in order, first name wins); auto: <repo>/campaths (gitignored, user-supplied).
    campaths_dir: str | list[str] = "auto"


@dataclass
class RecordingConfig:
    fps: int = 60
    width: int = 1920
    height: int = 1080
    crf: int = 23
    lead_in_seconds: float = 4.0
    lead_out_seconds: float = 3.0
    death_notices_duration: int = 5
    clean_hud: bool = True  # cl_draw_only_deathnotices 1
    # SteamID64 -> display name, applied with `mirv_replace_name byXuid` (HUD only).
    player_names: dict[str, str] = field(default_factory=dict)
    # Extra console commands run at every clip's setup tick (e.g. depth-of-field settings).
    extra_commands: list[str] = field(default_factory=list)
    # Kill-feed team colors as "#RRGGBB" or "#RRGGBBAA" (mirv_deathmsg colors); "" = game default.
    killfeed_ct_color: str = ""
    killfeed_t_color: str = ""
    # Hide the kill-feed row backgrounds and the red border around the local player's rows.
    killfeed_plain: bool = True
    # Per-part overrides ("#RRGGBB[AA]" or "R G B A"); win over killfeed_plain.
    killfeed_background_color: str = ""
    killfeed_background_local_color: str = ""
    killfeed_border_color: str = ""


@dataclass
class SelectionConfig:
    top: int = 10
    max_duration: float = 0.0  # seconds of final video, 0 = unlimited
    min_kills: int = 2
    kill_gap_seconds: float = 20.0  # max gap between kills inside one moment
    merge_gap_seconds: float = 5.0  # sequences closer than this get merged


@dataclass
class FaceitConfig:
    api_key: str = ""  # Data API server-side key; FACEIT_API_KEY env var wins


@dataclass
class Config:
    paths: PathsConfig = field(default_factory=PathsConfig)
    recording: RecordingConfig = field(default_factory=RecordingConfig)
    selection: SelectionConfig = field(default_factory=SelectionConfig)
    faceit: FaceitConfig = field(default_factory=FaceitConfig)


def _apply_section(section_obj: object, data: dict) -> None:
    valid = {f.name for f in fields(section_obj)}  # type: ignore[arg-type]
    for key, value in data.items():
        if key in valid:
            setattr(section_obj, key, value)


def load_config(path: Path | None = None) -> Config:
    config = Config()
    file_path = path or config_file_path()
    if file_path.is_file():
        data = tomllib.loads(file_path.read_text(encoding="utf-8"))
        for section_name in ("paths", "recording", "selection", "faceit"):
            if isinstance(data.get(section_name), dict):
                _apply_section(getattr(config, section_name), data[section_name])
    return config
