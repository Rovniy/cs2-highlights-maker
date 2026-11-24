"""Parse a CS2 demo with demoparser2 into plain model objects.

Everything downstream (analysis, recording) works off DemoData so the
demoparser2 API surface is isolated here.
"""

from __future__ import annotations

import gzip
import math
import shutil
import tempfile
from bisect import bisect_right
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from demoparser2 import DemoParser

from highlights.models import Kill, Round

DEFAULT_TICKRATE = 64.0


class DemoParseError(Exception):
    """A demo file could not be read."""


def is_parser_error(exc: BaseException) -> bool:
    """True for anything demoparser2 can throw at us, including Rust panics.

    demoparser2 is Rust: bad input can raise pyo3_runtime.PanicException, which
    derives from BaseException and so slips past `except Exception`. The module
    cannot be imported, so identify the panic by class name and module.
    KeyboardInterrupt/SystemExit/GeneratorExit must never count.
    """
    if isinstance(exc, Exception):
        return True
    return type(exc).__name__ == "PanicException" and type(exc).__module__ == "pyo3_runtime"

# Side strings as demoparser2 reports them.
_SIDE_BY_TEAM_NUM = {2: "T", 3: "CT"}


@dataclass(frozen=True)
class Death:
    """Unfiltered death record, used for alive-tracking (clutch detection)."""

    tick: int
    round_index: int
    victim_steamid: str


@dataclass(frozen=True)
class Blind:
    """A player blinded by a flashbang (player_blind); `entity` is the flashbang's entity id."""

    tick: int
    attacker_steamid: str
    victim_steamid: str
    duration: float
    entity: int


@dataclass(frozen=True)
class NadeHit:
    """Damage dealt by an HE grenade or fire (player_hurt with a grenade weapon)."""

    tick: int
    attacker_steamid: str
    victim_steamid: str
    weapon: str  # "hegrenade", "inferno", "molotov", "incgrenade"
    damage: int


@dataclass(frozen=True)
class Detonation:
    """An HE or flash going off, or fire starting (inferno_startburn); `entity` is the projectile's
    entity id for HE/flash (the fire has its own id, so molotovs are matched by thrower and time)."""

    tick: int
    kind: str  # "he", "flash", "fire"
    thrower_steamid: str
    entity: int
    pos: tuple[float, float, float]


NADE_WEAPONS = ("hegrenade", "inferno", "molotov", "incgrenade")
DETONATION_EVENTS = {"hegrenade_detonate": "he", "flashbang_detonate": "flash", "inferno_startburn": "fire"}


@dataclass
class DemoData:
    path: Path
    map_name: str
    tickrate: float
    kills: list[Kill]  # filtered: no warmup, teamkills, suicides
    deaths: list[Death]  # unfiltered: every player death after match start
    rounds: list[Round]
    player_names: dict[str, str]  # steamid -> name
    slots: dict[str, int]  # steamid -> spec_player slot (may be empty, see get_slots)
    rosters: dict[int, dict[str, str]] = field(default_factory=dict)  # round -> {steamid: side}
    # round -> {steamid: equipment value at freeze end} (empty when the demo lacks the prop)
    equipment: dict[int, dict[str, int]] = field(default_factory=dict)
    blinds: list[Blind] = field(default_factory=list)  # flashbang blinds after match start
    nade_hits: list[NadeHit] = field(default_factory=list)  # HE / fire damage after match start
    detonations: list[Detonation] = field(default_factory=list)  # HE / flash pops and fires starting
    match_start_tick: int = 0
    last_tick: int = 0

    def round_at(self, index: int) -> Round | None:
        if 0 <= index < len(self.rounds):
            return self.rounds[index]
        return None


def ensure_dem_file(path: Path, work_dir: Path) -> Path:
    """Demos often arrive compressed (.gz from FACEIT, .zst from Valve);
    transparently decompress to the work dir."""
    suffix = path.suffix.lower()
    if suffix not in (".gz", ".zst"):
        return path
    target = work_dir / path.name[: -len(suffix)]
    if not target.suffix:
        target = target.with_suffix(".dem")
    target.parent.mkdir(parents=True, exist_ok=True)
    if suffix == ".gz":
        with gzip.open(path, "rb") as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)
    else:
        import zstandard

        with open(path, "rb") as src, open(target, "wb") as dst:
            zstandard.ZstdDecompressor().copy_stream(src, dst)
    return target


def _normalize_side(value: object) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # NaN: a round with no winner (e.g. the last round of a demo cut off mid-round)
        return _SIDE_BY_TEAM_NUM.get(int(value), "") if value == value else ""
    text = str(value or "").upper()
    if text.startswith("T"):  # TERRORIST / T
        return "T"
    if text == "CT":
        return "CT"
    return ""


def _normalize_winner(value: object) -> str:
    return _normalize_side(value)


def _safe_event(parser: DemoParser, name: str, **kwargs) -> pd.DataFrame:
    try:
        df = parser.parse_event(name, **kwargs)
    except BaseException as exc:
        if not is_parser_error(exc):
            raise
        return pd.DataFrame()
    if not isinstance(df, pd.DataFrame):  # demoparser2 returns [] (a list) for an event the demo never fires
        return pd.DataFrame()
    return df


def _parser_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("demoparser2")
    except PackageNotFoundError:
        return "(unknown version)"


def _col(row: pd.Series, name: str, default=None):
    value = row.get(name, default)
    if pd.isna(value):
        return default
    return value


def _compute_tickrate(parser: DemoParser, sample_ticks: list[int]) -> float:
    """tickrate = Δtick / Δgame_time between two distant ticks; demo header has no rate."""
    if len(sample_ticks) < 2:
        return DEFAULT_TICKRATE
    t1, t2 = min(sample_ticks), max(sample_ticks)
    if t2 - t1 < 256:
        return DEFAULT_TICKRATE
    try:
        df = parser.parse_ticks(["game_time"], ticks=[t1, t2])
    except BaseException as exc:
        if not is_parser_error(exc):
            raise
        return DEFAULT_TICKRATE
    if df.empty:
        return DEFAULT_TICKRATE
    grouped = df.groupby("tick")["game_time"].first()
    if len(grouped) < 2:
        return DEFAULT_TICKRATE
    dt = grouped.index.max() - grouped.index.min()
    dgt = grouped.loc[grouped.index.max()] - grouped.loc[grouped.index.min()]
    if dgt <= 0:
        return DEFAULT_TICKRATE
    rate = dt / dgt
    if not math.isfinite(rate) or rate < 16 or rate > 256:
        return DEFAULT_TICKRATE
    return float(round(rate))


def _build_rounds(parser: DemoParser, match_start_tick: int) -> list[Round]:
    starts = _safe_event(parser, "round_start")
    ends = _safe_event(parser, "round_end")
    freeze_ends = _safe_event(parser, "round_freeze_end")

    start_ticks = sorted(int(t) for t in starts.get("tick", pd.Series(dtype=int)))
    freeze_ticks = sorted(int(t) for t in freeze_ends.get("tick", pd.Series(dtype=int)))

    rounds: list[Round] = []
    if ends.empty:
        return rounds
    ends = ends.sort_values("tick")
    for _, row in ends.iterrows():
        end_tick = int(row["tick"])
        if end_tick < match_start_tick:
            continue
        prior_starts = [t for t in start_ticks if t <= end_tick]
        start_tick = prior_starts[-1] if prior_starts else match_start_tick
        freeze_in_round = [t for t in freeze_ticks if start_tick <= t <= end_tick]
        freeze_end_tick = freeze_in_round[-1] if freeze_in_round else start_tick
        rounds.append(
            Round(
                index=len(rounds),
                start_tick=start_tick,
                freeze_end_tick=freeze_end_tick,
                end_tick=end_tick,
                winner=_normalize_winner(row.get("winner")),
            )
        )
    return rounds


def get_slots(parser: DemoParser, sample_tick: int) -> dict[str, int]:
    """steamid -> spec_player slot. CSDM uses slot = demo userid + 1 for CS2.

    Empirically unverified prop availability across demo types, so this is
    best-effort: returns {} when the prop is missing and the recorder warns.
    """
    try:
        df = parser.parse_ticks(["user_id"], ticks=[sample_tick])
    except BaseException as exc:
        if not is_parser_error(exc):
            raise
        return {}
    if df.empty or "user_id" not in df.columns:
        return {}
    slots: dict[str, int] = {}
    for _, row in df.iterrows():
        steamid = str(row.get("steamid", ""))
        user_id = row.get("user_id")
        if steamid and pd.notna(user_id):
            slots[steamid] = int(user_id) + 1
    return slots


def parse_demo(path: Path) -> DemoData:
    """Parse a demo, raising only DemoParseError for unreadable input.

    demoparser2 can panic (PanicException, a BaseException) on corrupt files,
    so the raw call is fenced in here and every parser failure becomes a
    DemoParseError with a readable message. KeyboardInterrupt and friends
    propagate untouched.
    """
    if not path.exists():
        raise DemoParseError(f"{path.name}: file not found")
    if path.stat().st_size == 0:
        raise DemoParseError(f"{path.name}: file is empty")
    try:
        return _parse_demo(path)
    except DemoParseError:
        raise
    except BaseException as exc:
        if not is_parser_error(exc):
            raise
        detail = str(exc)[:120]
        raise DemoParseError(
            f"{path.name}: could not be read ({type(exc).__name__}: {detail}) "
            "— is it a complete CS2 demo?"
        ) from exc


def _parse_demo(path: Path) -> DemoData:
    parser = DemoParser(str(path))

    try:
        header = parser.parse_header()
    except BaseException as exc:
        if not is_parser_error(exc):
            raise
        header = {}
    map_name = str(header.get("map_name", "unknown"))

    new_match = _safe_event(parser, "begin_new_match")
    match_start_tick = 0
    if not new_match.empty and "tick" in new_match.columns:
        match_start_tick = int(new_match["tick"].max())

    try:
        deaths_df = parser.parse_event("player_death", player=["team_name"], other=["is_warmup_period"])
    except BaseException as exc:
        if not is_parser_error(exc):
            raise
        # A valid demo the installed demoparser2 cannot decode, typically right after a CS2 update.
        raise DemoParseError(
            f"{path.name}: demoparser2 {_parser_version()} could not read its kills ({exc}) — after a CS2 update "
            "this usually needs a newer demoparser2: pip install -U demoparser2"
        ) from exc
    if not isinstance(deaths_df, pd.DataFrame) or deaths_df.empty:
        raise DemoParseError(f"{path.name}: no kills found — is this a valid CS2 demo?")
    deaths_df = deaths_df.sort_values("tick")

    rounds = _build_rounds(parser, match_start_tick)
    if not rounds:
        raise DemoParseError(f"{path.name}: no rounds found — demo looks incomplete")
    round_start_ticks = [r.start_tick for r in rounds]

    def round_index_for_tick(tick: int) -> int:
        idx = bisect_right(round_start_ticks, tick) - 1
        return max(0, idx)

    kills: list[Kill] = []
    deaths: list[Death] = []
    for _, row in deaths_df.iterrows():
        tick = int(row["tick"])
        if tick < match_start_tick:
            continue
        if bool(_col(row, "is_warmup_period", False)):
            continue
        victim_steamid = str(_col(row, "user_steamid", "") or "")
        round_index = round_index_for_tick(tick)
        if victim_steamid:
            deaths.append(Death(tick=tick, round_index=round_index, victim_steamid=victim_steamid))

        attacker_steamid = str(_col(row, "attacker_steamid", "") or "")
        if not attacker_steamid or attacker_steamid == victim_steamid:
            continue  # suicide / world death
        attacker_side = _normalize_side(_col(row, "attacker_team_name", ""))
        victim_side = _normalize_side(_col(row, "user_team_name", ""))
        if attacker_side and victim_side and attacker_side == victim_side:
            continue  # teamkill
        kills.append(
            Kill(
                tick=tick,
                round_index=round_index,
                attacker_steamid=attacker_steamid,
                attacker_name=str(_col(row, "attacker_name", "") or ""),
                attacker_side=attacker_side,
                victim_steamid=victim_steamid,
                victim_name=str(_col(row, "user_name", "") or ""),
                weapon=str(_col(row, "weapon", "") or ""),
                headshot=bool(_col(row, "headshot", False)),
                noscope=bool(_col(row, "noscope", False)),
                penetrated=int(_col(row, "penetrated", 0) or 0),
                thrusmoke=bool(_col(row, "thrusmoke", False)),
                attackerblind=bool(_col(row, "attackerblind", False)),
            )
        )

    player_names: dict[str, str] = {}
    try:
        info = parser.parse_player_info()
        for _, row in info.iterrows():
            steamid = str(row.get("steamid", ""))
            if steamid:
                player_names[steamid] = str(row.get("name", ""))
    except BaseException as exc:
        if not is_parser_error(exc):
            raise
        pass
    for kill in kills:
        player_names.setdefault(kill.attacker_steamid, kill.attacker_name)
        player_names.setdefault(kill.victim_steamid, kill.victim_name)

    death_ticks = [d.tick for d in deaths]
    tickrate = _compute_tickrate(parser, death_ticks)
    last_tick = max(
        rounds[-1].end_tick,
        death_ticks[-1] if death_ticks else 0,
    ) + int(10 * tickrate)

    rosters, equipment = _build_rosters(parser, rounds)
    blinds, nade_hits, detonations = _grenade_events(parser, match_start_tick)
    slots = get_slots(parser, rounds[0].freeze_end_tick + 16)

    return DemoData(
        path=path,
        map_name=map_name,
        tickrate=tickrate,
        kills=kills,
        deaths=deaths,
        rounds=rounds,
        player_names=player_names,
        slots=slots,
        rosters=rosters,
        equipment=equipment,
        blinds=blinds,
        nade_hits=nade_hits,
        detonations=detonations,
        match_start_tick=match_start_tick,
        last_tick=last_tick,
    )


def _grenade_events(
    parser: DemoParser, match_start_tick: int
) -> tuple[list[Blind], list[NadeHit], list[Detonation]]:
    """Flashbang blinds, grenade damage and detonations (used to find grenades worth a grenade cam)."""
    blinds: list[Blind] = []
    df = _safe_event(parser, "player_blind")
    for _, row in df.iterrows():
        tick = int(row["tick"])
        if tick < match_start_tick:
            continue
        blinds.append(
            Blind(
                tick=tick,
                attacker_steamid=str(_col(row, "attacker_steamid", "") or ""),
                victim_steamid=str(_col(row, "user_steamid", "") or ""),
                duration=float(_col(row, "blind_duration", 0.0) or 0.0),
                entity=int(_col(row, "entityid", -1) or -1),
            )
        )
    hits: list[NadeHit] = []
    df = _safe_event(parser, "player_hurt")
    if not df.empty and "weapon" in df.columns:
        for _, row in df[df["weapon"].isin(NADE_WEAPONS)].iterrows():
            tick = int(row["tick"])
            if tick < match_start_tick:
                continue
            hits.append(
                NadeHit(
                    tick=tick,
                    attacker_steamid=str(_col(row, "attacker_steamid", "") or ""),
                    victim_steamid=str(_col(row, "user_steamid", "") or ""),
                    weapon=str(row["weapon"]),
                    damage=int(_col(row, "dmg_health", 0) or 0),
                )
            )
    detonations: list[Detonation] = []
    for event, kind in DETONATION_EVENTS.items():
        df = _safe_event(parser, event)
        for _, row in df.iterrows():
            tick = int(row["tick"])
            if tick < match_start_tick:
                continue
            detonations.append(
                Detonation(
                    tick=tick,
                    kind=kind,
                    thrower_steamid=str(_col(row, "user_steamid", "") or ""),
                    entity=int(_col(row, "entityid", -1) or -1),
                    pos=(float(_col(row, "x", 0.0)), float(_col(row, "y", 0.0)), float(_col(row, "z", 0.0))),
                )
            )
    detonations.sort(key=lambda d: d.tick)
    return blinds, hits, detonations


def _build_rosters(
    parser: DemoParser, rounds: list[Round]
) -> tuple[dict[int, dict[str, str]], dict[int, dict[str, int]]]:
    """Alive players per side shortly after each round's freeze end, and their equipment value."""
    sample_by_tick: dict[int, int] = {}
    for r in rounds:
        sample_by_tick[r.freeze_end_tick + 16] = r.index
    df = None
    for props in (["is_alive", "team_num", "current_equip_value"], ["is_alive", "team_num"]):
        try:
            df = parser.parse_ticks(props, ticks=list(sample_by_tick.keys()))
            break
        except BaseException as exc:
            if not is_parser_error(exc):
                raise
    if df is None:
        return {}, {}
    rosters: dict[int, dict[str, str]] = {r.index: {} for r in rounds}
    equipment: dict[int, dict[str, int]] = {}
    if df.empty:
        return rosters, equipment
    for _, row in df.iterrows():
        tick = int(row["tick"])
        round_index = sample_by_tick.get(tick)
        if round_index is None:
            continue
        if not bool(row.get("is_alive", False)):
            continue
        side = _normalize_side(row.get("team_num"))
        if side not in ("CT", "T"):
            continue
        steamid = str(row.get("steamid", ""))
        if steamid:
            rosters[round_index][steamid] = side
            value = row.get("current_equip_value")
            if value is not None and not pd.isna(value):
                equipment.setdefault(round_index, {})[steamid] = int(value)
    return rosters, equipment
