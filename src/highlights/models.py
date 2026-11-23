"""Core data models shared across parsing, analysis and recording."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Kill:
    tick: int
    round_index: int  # 0-based index into DemoData.rounds
    attacker_steamid: str
    attacker_name: str
    attacker_side: str  # "CT" / "T" / ""
    victim_steamid: str
    victim_name: str
    weapon: str
    headshot: bool = False
    noscope: bool = False
    penetrated: int = 0
    thrusmoke: bool = False
    attackerblind: bool = False

    @property
    def is_knife(self) -> bool:
        return "knife" in self.weapon or self.weapon == "bayonet"

    @property
    def is_zeus(self) -> bool:
        return self.weapon == "taser"

    @property
    def is_awp_noscope(self) -> bool:
        return self.weapon == "awp" and self.noscope

    @property
    def is_special(self) -> bool:
        return (
            self.is_knife
            or self.is_zeus
            or self.is_awp_noscope
            or self.thrusmoke
            or self.attackerblind
        )


@dataclass(frozen=True)
class Round:
    index: int  # 0-based
    start_tick: int
    freeze_end_tick: int
    end_tick: int  # tick of round_end (round decided)
    winner: str  # "CT" / "T" / ""


@dataclass
class Clutch:
    steamid: str
    name: str
    side: str
    opponents: int  # X in "1 v X"
    won: bool
    kills: int  # kills made by the clutcher after the clutch started
    start_tick: int
    end_tick: int  # round_end tick
    round_index: int


@dataclass
class Moment:
    """A burst of action by one player in one round; the unit that gets scored."""

    steamid: str
    name: str
    side: str
    round_index: int
    kills: list[Kill] = field(default_factory=list)
    clutch: Clutch | None = None
    score: float = 0.0
    tags: list[str] = field(default_factory=list)  # round context that changed the score (match point, eco)

    @property
    def kill_count(self) -> int:
        return len(self.kills)

    @property
    def first_tick(self) -> int:
        ticks = [k.tick for k in self.kills]
        if self.clutch is not None:
            ticks.append(self.clutch.start_tick)
        return min(ticks)

    @property
    def last_tick(self) -> int:
        ticks = [k.tick for k in self.kills]
        # A won clutch is worth watching until the round is decided.
        if self.clutch is not None and self.clutch.won:
            ticks.append(self.clutch.end_tick)
        return max(ticks)

    def describe(self) -> str:
        parts: list[str] = []
        if self.kill_count > 0:
            weapons = {k.weapon for k in self.kills}
            weapon = next(iter(weapons)) if len(weapons) == 1 else None
            kills_part = f"{self.kill_count}K"
            if self.kill_count == 5:
                kills_part = "ACE"
            if weapon:
                kills_part += f" ({weapon})"
            parts.append(kills_part)
        if self.clutch is not None and self.clutch.won:
            parts.append(f"1v{self.clutch.opponents} clutch")
        if any(k.is_knife for k in self.kills):
            parts.append("knife")
        if any(k.is_zeus for k in self.kills):
            parts.append("zeus")
        if any(k.is_awp_noscope for k in self.kills):
            parts.append("noscope")
        return ", ".join(parts) if parts else "moment"


@dataclass(frozen=True)
class CameraAction:
    tick: int
    steamid: str


@dataclass
class Sequence:
    """A clip to record: demo tick range plus a camera plan."""

    number: int
    start_tick: int
    end_tick: int
    cameras: list[CameraAction]
    label: str
    score: float
    round_index: int
    moments: list[Moment] = field(default_factory=list)
    # Cinematic re-recording of another sequence's ticks through a user cam path.
    campath: Path | None = None
    cinematic_of: int | None = None
    # Clip-second ranges where the cinematic replaces the POV footage (splice mode).
    splice_segments: list[tuple[float, float]] | None = None
    # --campath-hold/--campath-align: the cam path is started offset_seconds late and holds its
    # last pose until hold_until_seconds, as {"offset_seconds", "hold_until_seconds"}.
    campath_retime: dict | None = None
    # --slow-mode auto: the slow-motion beat, {"kill_tick", "start_seconds", "end_seconds", "speed",
    # "worth", "reason", "victim"} (clip seconds); applied to the finished POV clip.
    slowmo: dict | None = None
    # --tilt-mode: the cam-path footage of this take gets a tilt-shift look, {"distance", "height", "reason"}.
    tilt: dict | None = None
    # Top cam path candidates considered for this play (name, distance, coverage).
    cinematic_candidates: list[dict] | None = None
    # Generated-cam segments (start, end, kind, style) of a --intro-cam/--downtime-cam take.
    dynamic_cam: list[dict] | None = None
    # --static-cam take: {"name", "label", "slug", "deaths_seen", "player_seconds", "distance"}; recorded as
    # its own file, never spliced or concatenated.
    static_cam: dict | None = None
    # --static-downtime take: the down-time passes this static cam won, [{"start_seconds", "end_seconds",
    # "shows" ("pov"/"enemies"), "cam", "slug", "player_share", "enemy_share", "enemies_avg", "enemies_seen"}];
    # spliced into the POV clip like a generated cam.
    static_downtime: list[dict] | None = None
    # --xray take: the whole play recorded with spec_show_xray 1, cut over the POV clip around the kills that
    # earn it with a dissolve, {"windows": [{"start_seconds", "end_seconds", "fade_in", "fade_out", "kill_ticks",
    # "reasons"}], "fade_seconds", "mode"}; splice_segments holds the full-strength spans.
    xray: dict | None = None

    @property
    def name(self) -> str:
        return f"{self.number}-sequence"

    def duration_seconds(self, tickrate: float) -> float:
        return (self.end_tick - self.start_tick) / tickrate

    @property
    def featured_steamids(self) -> list[str]:
        seen: list[str] = []
        for camera in self.cameras:
            if camera.steamid not in seen:
                seen.append(camera.steamid)
        return seen
