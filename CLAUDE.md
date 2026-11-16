# CLAUDE.md

CS2 demo → highlights MP4 generator. Pipeline: parse (demoparser2) → detect
moments → record real footage by driving CS2+HLAE → FFmpeg post-process/concat.
Windows-only at the recording stage; analysis runs anywhere.

## Commands

```powershell
.venv\Scripts\python -m pytest tests -q                  # unit tests (synthetic fixtures, no demo needed)
.venv\Scripts\highlights doctor                          # environment diagnostic (run before touching recording)
.venv\Scripts\highlights <demo> --json-only -o out.mp4   # analysis smoke test, writes out.json + rich table
.venv\Scripts\highlights <demo> --json-only --cinematic   # preview cam-path matches (needs files in campaths/)
.venv\Scripts\highlights record-one <demo> --start-tick A --end-tick B --steamid S   # hidden: single-clip recording PoC
.venv\Scripts\highlights update-tools                    # re-download latest HLAE/FFmpeg after a CS2 update
```

`highlights top DEMO|FOLDER... [--recursive] [--players ..] [--top N] [--round N|DEMO:N] [--pick 1,2 --exclude 3]` pools and ranks moments across demos and
prints one table only (no JSON, no recording); players missing from a demo skip that demo.
`--build-places` (`cli._add_missing_places`): maps with no saved place map get one from every demo of that map in the run (same sampling as `places build`, `_place_samples`); saved maps are never touched. `--rebuild-places`: every map in the run starts empty and its saved map is overwritten.
Its Length column is `selection.clip_seconds` per row (lead-in/out included), followed by a `Total: 4m 05s` line
(`report.minutes_seconds`, exact sum; rough for the finished edit: slow-mo, trims and transitions change it).

`highlights scan FOLDER --players .. --min-score N [--recursive -o FILE]` (`scan.py`: `find_demos`, `classify`, `write_scan`):
parses every demo, `eligible_moments` for the players, keep when a moment scores >= N; writes a path list + `.json`; `--delete` (+ `--yes`) permanently deletes the `remove` verdicts after listing/confirming (`delete_demos`).

`run` also takes `--round` (filter in `cli._analyze`, before `select_sequences`).

`run`, `top` and `batch` take `--weapon NAME,.. --headshot-only --per-kill --lead-in S --lead-out S` (`cli._kill_filter_options`,
`analysis/killfilter.py`): `apply_kill_filter` rebuilds the scored moments from their matching kills (demo weapon names,
`ALIASES` for short names, `knife` = every knife) and rescores them; `--per-kill` makes one single-kill moment per kill
(no clutch) with `PER_KILL_LEAD_IN`/`OUT` 2 s / 1.5 s. Any filter makes single kills qualify unless `--min-kills` is
given. Batch JSON gets `batch.kill_filter`. On `run` (`cli._analyze`), `--per-kill` turns off the overlap merge
(`select_sequences(merge=False)`), so each kill is its own segment of the video even when clips overlap.

CLI: click with a custom `DefaultRunGroup` — `highlights match.dem` resolves to
the `run` subcommand. Entry point `highlights.cli:main`.

## Architecture (src/highlights/)

- `demo/parser.py` — the ONLY file touching the demoparser2 API; produces
  `DemoData` (kills, rounds, rosters, slots, tickrate). Tickrate is computed
  from Δtick/Δgame_time (not in the header), fallback 64.
- `analysis/` — `moments.py` (cluster kills per player/round), `clutch.py`
  (replay deaths vs rosters → 1vX), `scoring.py` (all knobs in `WEIGHTS`),
  `selection.py` (top-N, clip tick bounds, overlap merge + camera plan).
- `recording/` — port of CS Demo Manager's (MIT) recording flow:
  `steam.py` (registry+vdf autodetect), `toolchain.py` (HLAE/FFmpeg download),
  `plugin.py` (server.dll install + gameinfo.gi patch), `actions_file.py`
  (`<demo>.json` the plugin executes), `sequences.py` (per-clip command plan),
  `hlae.py` (launch/inject/wait/crash-detect), `recorder.py` (orchestrator),
  `campaths.py` (`--cinematic`: load user cam paths, match them to plays).
- `spatial.py` — per-clip world-coordinate metadata (engagements, zones,
  keyframes, POV trajectories) sampled from the demo; feeds the report and
  cam-path matching. The only other file besides `parser.py` using demoparser2.
- `report.py` — the JSON report (`schema_version` 3). `write_report` runs
  BEFORE recording (timeline events with `seconds_from_clip_start` and nominal
  frame estimates); `finalize_report` runs AFTER concat and adds
  `video_start_seconds`, per-kill `video_time_seconds` and a flat
  `beat_targets` list (POV kills only) from ffprobe clip durations, crossfade
  overlap and `--timing-offset`. Times are nominal until calibrated against a
  real clip (`editing.timing_status`).
- `places.py` — per-map position→place-name lookup (`PlaceMap`, 64×64×96 unit cells, nearest
  populated cell wins, height weighted ×2) saved in `~/.cs2-highlights/places/<map>.json`;
  built by `highlights places build DEMO...` (accumulates across runs), `places list` shows it.
  `route_of(points, place_map)` turns positions into a compressed place sequence
  (e.g. `Silo > Outside > Mini > BombsiteA`) — used to describe clips and cam paths.
- Cam path folders: `[paths] campaths_dir` is one folder or a list (`campaths.resolve_campaths_dirs`, "auto" = the
  repo's `campaths/`). `map_folders` takes the list, so `load_campaths`/`load_static_cams`/`load_labels` read every
  folder in order; `first_by_name` keeps the first path/static cam per case-insensitive name (names are ids), labels
  likewise.
- `recording/path_routes.py` — `path_route(path, place_map)` (places a cam path passes through)
  and `load_labels` (optional `campaths/<Map>/labels.toml`, file name → friendly name); both show
  in the `--cinematic` candidate lists and in `cinematic.candidates` (`route`, `label`). Log text
  containing `[...]` must go through `rich.markup.escape` (info() renders markup).
- `recording/suggest.py` — `suggest_cinematics(...)`: per clip verdict (good/possible/none), why,
  best cam path + alternatives (coverage seconds, route, route overlap). Runs the splice matching
  quietly (`add_cinematic_sequences(..., quiet=True)`) so it can never disagree with what
  `--cinematic-mode splice` would record. CLI: `--suggest-cinematic` (preview table + report key
  `cinematic_suggestion`; not combinable with `--cinematic`). Results are keyed by the INPUT
  sequence numbers (the cinematic pass renumbers its output).
- `recording/dyncams.py` + `dyn_sequences.py` — `--intro-cam` / `--downtime-cam`: generated cam
  paths (face/profile/gun/tps/shoulder/heli) computed from dense POV tracks
  (`spatial.sample_pov_tracks`, 16 Hz, yaw/pitch) as an extra cinematic take per play
  (`Sequence.dynamic_cam`, `splice_segments`, report `cinematic.generated_cams`). Intro
  segments hold the style `Shot`, spiral round to just behind the head (`SWOOP_END`) and push
  into the exact first-person eye pose (z + `EYE_HEIGHT` 64; `sample_pov_tracks` lowers z by
  `duck_amount` x `CROUCH_EYE_DROP` 18 so a crouching player's eye is 46) so the
  hand-off is seamless (`--intro-transition cut` → `Segment.swoop=False`: the intro holds the style shot to
  its end and hard-cuts like a down-time pass; report `transition`); down-time segments are one continuous hard-cut pass of the shot
  (>= `DOWNTIME_MIN` 2.5 s, <= `DOWNTIME_MAX` 12 s, the `MAX_DOWNTIME_PASSES` 2 longest per
  clip). One style per kind per clip. Paths are
  rebased to the first recorded frame, so a path whose first segment starts late holds that
  first pose from t=0. `plan_segments` reuses the splice kill padding, avoids ranges a real
  splice take covers, and splits at camera switches. `cli._splice_cinematics` overlays
  real takes first, generated ones on top; append-mode takes stay in the list. Styles are
  offset estimates (constants/`style_pose`), no wall check, `POV_FOV` and in-game model
  visibility unvalidated. Not combinable with `--suggest-cinematic` (renumbering).
- `recording/static_cams.py` — `--static-cam auto|all|NAME,..` (run and batch): fixed cams from
  `campaths/<Map>/static.toml` (`getpos` line or pos/angles) and `static/*.campath` (first point). Per play
  `assess` = selected-kill victims dying in view + seconds of the POV player in view (`splice_plan._in_view`)
  + distance; auto = best, all = every cam seeing a death or >= MIN_SEGMENT of the player. Each take is a
  generated 4-point hold path (`dyncams.write_campath`, which refuses < `MIN_POINTS` 4: HLAE cubic interpolation needs 4), `Sequence.static_cam`, added after tilt/slow-mo
  via `insert_takes` (now takes a list per POV). Never spliced or concatenated: `run` copies it to
  `<output>-clipNN-static-<slug>.mp4`, batch to `<name>-static-<slug>.mp4`. Unvalidated in-game.
- `recording/static_downtime.py` — `--static-downtime auto|pov|enemies` (run and batch, not with `--downtime-cam`):
  passes = `dyncams.plan_segments(..., None, "static", taken)` (taken = the POV's other takes' splice segments). Per pass
  `assess_pass` every static cam at `STEP` 0.5 s: POV player share (`_target_at`) and living enemies in view (all
  players from ONE `broll.sample_players` query over every pass tick, skipped for `pov`; enemies = roster side differs
  from the active camera's player). `choose`: pov >= `MIN_PLAYER_SHARE`, enemies >= `MIN_ENEMY_SHARE`, auto = enemies
  when avg >= `AUTO_ENEMIES` 2. One take per winning cam (4-point hold, `Sequence.static_downtime`, splice_segments =
  its passes), added after the generated cams (before grenade/tilt; tilt skips them). `_splice_cinematics` order:
  cam path, static down time, generated. Uses `floors`.
- `recording/grenade_cam.py` — `--grenade-cam auto|all` (run and batch): `spatial.grenade_flights` (the one
  `parse_grenades` query; projectile samples per tick, split into flights at tick gaps because entity ids are
  reused) → `detonate` cuts each flight at its detonation (`DemoData.detonations` from hegrenade_detonate /
  flashbang_detonate by entity id, inferno_startburn by thrower + time: the projectile entity outlives the
  pop by seconds) → `grenade_worth` (HE/fire kill or hits on `MIN_HURT` enemies from `nade_hits`, flash blind
  `MIN_BLIND` on an enemy killed within `FLASH_KILL_WINDOW`, matched by the flash's entity id in `blinds`) →
  `plan_clip` (thrower = active camera, inside the clip incl. `HOLD`, non-overlapping) → `follow_keyframes`
  (behind/above along the smoothed flight, hold on `burst`) → one take per clip via
  `dyn_sequences.insert_takes` (after the other takes, so it is spliced on top). No wall check; unvalidated.
- `routes.py` — `clip_route(sequence, spatial)`: the places the POV player moves through
  (from the place hints already sampled in spatial metadata) and where each selected kill
  landed; stored as `spatial.route` and shown as the "Where" column of the highlights table.
- `analysis/slowmo.py` + `video/slowmo.py` — `--slow-mode auto` on `run` and `top`: `best_kill` picks a
  moment's most slow-mo-worthy kill (`KILL_WORTH`, 4K/ace/clutch finishers, `MIN_WORTH`), `choose` keeps the
  best `MAX_FRACTION` (a third, rounded up), `plan_sequences` stores `Sequence.slowmo` on POV sequences
  (window `LEAD`/`TAIL` around the kill, `SPEED` 0.4; drops the lowest-worth beats to fit `--max-duration`).
  `cli._apply_slowmo` retimes the window with FFmpeg after splice, before concat; `finalize_report` remaps
  event/beat times through it (`slowmo.remap`). Constant speed, no frame interpolation, no ramp.
- `analysis/tilt.py` + `video/tiltshift.py` — `--tilt-mode auto|on` (run and batch; needs splice mode): `plan_sequences`
  sets `Sequence.tilt` on real cam-path takes (never `dynamic_cam` ones). `auto` = mean camera-to-player distance
  >= `MIN_DISTANCE` 700 and height >= `MIN_HEIGHT` 150 over the splice segments (pose via `retime`d path), best
  `MAX_FRACTION` 0.5 of the plays; `on` = all. `splice_clip(..., tilt=True)` runs `tilt_filter(width, height)` (blur on a
  quarter-size copy, one-frame gradient mask + `maskedmerge`, `eq` saturation; a per-pixel `blend` expression was ~25x
  slower than a plain pass at 1080p120, so keep expressions off the per-frame path) on the cinematic input before the overlay. Look constants
  are untuned.
- `broll.py` — `highlights broll DEMO... --mode empty|players [--count --max-length --seed --cinematic-path]`: cam-path-only
  clips for pre-edit build-up. `plan_demo_broll` randomly tries (round, start, path) windows in the live phase, with
  all players' positions sampled every 0.5 s in ONE `_query_positions` call (`sample_players`, injectable for tests);
  `empty` = nobody alive in view (`splice_plan._in_view` with `EMPTY_MARGIN` 1.15), `players` = in view for
  >= `MIN_PRESENCE` 0.6 of the samples. Sequences have `campath` set and `cinematic_of` None (the camera player only seeds
  setup). Output `<label>-broll-<mode>-<n>.mp4/.json` (`kind: broll`, own small JSON, not the cotw report shape).
  No wall check; recording unvalidated in-game.
- `batch.py` — `highlights batch DEMO|FOLDER... [--recursive] [--players/--team --names-file] [--round N|DEMO:N] --top N -o DIR`: global best-N
  (`selection.pick_rows`, shared with `top`) over all demos, one isolated sequence per moment
  (`build_sequences(..., merge=False)`), cinematic (always splice) + generated cams per demo, ONE
  recording session per demo (`record`), then each POV clip is copied to `<label>-<player slug>-<n>.mp4` (JSON `batch` = `rank_block`: rank/of/score/relative_score over the whole batch) and
  its group (POV + takes, renumbered 1..k) gets `<name>.json` via `write_report`/`finalize_report`.
  Player filter is lenient (absent players ignored, `present_players`). No slow-mo (cotw does it, needs 120 fps).
- `teaser.py` — `batch --cinematic-only [--speed 1 --clip-length 5 --output-fps]` (fps defaults to 240): `plan_demo(protect_kills=False)`
  makes `splice_segments` the raw cam-path visibility runs (kills included); `plan_takes` keeps only the cinematic take per
  clip, `choose_window` picks the stretch at the kills, the take is ended after the window (`TAIL`), POVs are never recorded;
  `record_teaser` cuts the window and slows it with one ffmpeg pass (`slow_args`: `setpts=PTS/speed,fps=`, no audio) ->
  `<name>.mp4` + `kind: teaser` JSON. Unvalidated in-game.
- Round context (`analysis/scoring.py`): `round_context` derives the score before each round from
  `rosters` (team A = CT players of the first rostered round, followed across side switches) and
  `wins_needed` (13, then 16/19/... for MR3 overtimes) → `match_point`; `DemoData.equipment` (round →
  steamid → `current_equip_value` at freeze end + 16, read with the rosters in `parser._build_rosters`)
  gives `eco`/`anti-eco` (`ECO_MAX` 1500 vs `BUY_MIN` 3500). Multipliers in `WEIGHTS`; the tags are
  stored on `Moment.tags` and shown after the highlight in the top tables.
- `--pick/--exclude` (`selection.parse_numbers`/`apply_picks`, top and batch): row numbers of the
  table the same command prints without them (after `pick_rows`); `print_top_table(numbers=)` keeps them.
- `batch.slowmo_hint` → `slowmo_suggestion` in each batch JSON: the `--slow-mode auto` pick (`best_kill` +
  `choose` over the whole batch), never applied; shown in the batch table's "Slow-mo" column.
- `batch --buildup` (`broll.plan_buildups`: same round incl. freeze time, latest window first, so the shot
  ends at the clip start; `<name>-buildup.*`, sequences numbered after the others and mapped by `id()`).
- `batch --resume` (skip clips whose mp4 exists) and `--reuse-recordings` (`record_or_reuse` +
  `<work>/recorded.json` of `_sequence_key`: ticks, cameras, cam path file sha1, retime, fps/size, extra
  commands, names; implies keeping the work dir).
- Kill-feed calibration in `batch` (`video/killfeed_sync.py`: change spikes in the kill-feed corner of the
  plain POV clip, before splicing, matched to nominal kill times; `measure_offset` needs `MIN_SHARE`
  agreement, one kill needs a single candidate) → per-clip `offset_seconds` +
  `editing.video_timing.kill_sync`, and the slow-mo hint shifted with it. UNVALIDATED on real footage.
- `video/review.py` + `video/frames.py` — after `batch`/`broll` and `highlights review DIR`: contact sheet per
  clip (`review/<name>.jpg`), `review/index.html`, and a heuristic clipping check on cam-path parts only
  (`checked_ranges` from the JSON; dark mean or >= 75% flat 4x4 blocks on 64x36 gray frames at 4 fps, runs
  of >= 2) stored as `review` in the JSON. No pre-recording geometry check exists (no map collision data).
- `ingame_check.py` — `highlights check-ingame DEMO`: records the best play as POV, cam path (hold), face
  intro and spliced clip and writes `CHECKLIST.md` (what to see when) to confirm the unvalidated assumptions.
- `floors.py` — level check for every view-cone test (`splice_plan._in_view(..., floors=)`: splice/align,
  `--suggest-cinematic`, static cams, b-roll/build-ups). `floors_for(demo, place_map)` samples all players once per
  second of live play (one `_query_positions` call; `cli._floors`, built once per demo in run/batch when cinematic or
  static cams are on) + place-map cells (middle height, so areas this demo never visits still have floors); counts per
  column in 32-unit height bins. Floor under a camera = highest bin <= camera z with >= `MIN_SAMPLES` within
  `SEARCH_RADII` 1/3/6 cells (nearest ring that has one); target < floor - `LEVEL_GAP` 200 = hidden. Tuned on Nuke
  (upper -416, B -768/-640); no data near the camera = no check. b-roll builds its floors from its own snapshots.
- `faceit.py` — FACEIT match context: match id from the demo file name / batch label
  (`1-<uuid>-<n>-<m>.dem`), Data API `/matches/{id}` (+ organizer name), key `[faceit] api_key` or
  `FACEIT_API_KEY`, finished matches cached in `~/.cs2-highlights/faceit/`. `run` and `batch` add a
  `match` block (teams, score, division = "ESEA Main" / "ESEA Open 1-8" (bracket kept) parsed from `competition_name`, else the competition name, `players` steamid → team/opponent,
  stand-ins via demo sides) after `write_report`; the cotw overlay tool reads it. Any failure = no block.
  `top` adds a Match column (`cli._match_cells`, row player's side, only demos in the table). `faceit.enabled` warns once without a key.
  `scan` stores `scan.match_summary` (no roster, + scanned player's team/opponent) per demo + Match column.
- `video/vertical.py` — `--vertical blur|crop` (+ `--vertical-killfeed`): final 1080x1920 copy
  `<output>-vertical.mp4` via one FFmpeg filter pass after the concat (kill-feed crop region constants
  assume the default top-right position).
- `video/` — wav→aac mux, optional drawtext overlay, concat demuxer
  (stream-copy default) or xfade chain.

## Demo parser errors

demoparser2 is Rust: bad input (e.g. an empty file) raises `pyo3_runtime.PanicException`, a
`BaseException` that `except Exception` does NOT catch (the module is not importable). Around
every demoparser2 call use `except BaseException as exc: if not is_parser_error(exc): raise`
(`demo/parser.py`; KeyboardInterrupt/SystemExit must keep propagating). `parse_demo` only ever
raises `DemoParseError`; commands show it as a one-line error (`run`) or skip that demo with a
warning (`top`, `places build`); `build_spatial_metadata` degrades to status "unavailable".

A CS2 update can make the installed demoparser2 fail on valid demos (Nov 2026: 0.42.0 raised `EntityNotFound` for
every `player_death` query on patch 14197 demos; fixed in 0.42.2, now the minimum in pyproject). `player_death` is
queried without `_safe_event` so that failure reports "needs a newer demoparser2" instead of "no kills found".

## Recording invariants — do not "simplify" these

Ported from CSDM source (verified June 2026; local reference copies in
`.research/csdm/`, gitignored). Each rule guards a real regression:

- Ticks in the actions file are clamped to ≥ 96 (game skips early ticks after
  `demo_gototick 0`, CSDM #1343).
- `demo_gototick` must run on an EARLIER tick than any `spec_player`
  (same-tick combo gets spec_player ignored since an Oct 2025 CS2 update,
  CSDM #1238), and `spec_mode 1` must precede `spec_player`.
- Camera slot = demo `user_id` + 1 (CSDM convention). Validated in-game:
  recorded clips show the intended POV player.
- `pause_playback` goes at `start_tick - 4` (engine may skip ticks between
  pause request and actual pause).
- FFmpeg preset string: `{QUOTE}` is HLAE's quote placeholder; output folder
  uses forward slashes; the literal `\\` before `video.mp4` is unescaped by
  HLAE. Don't normalize any of it.
- `-insecure` in game args is mandatory (the plugin hard-aborts without it).
- Plugin lifecycle: install copies server.dll to `<cs2>/game/csgo/csdm/bin/`
  and patches `gameinfo.gi` (backup → `gameinfo.gi.backup`); uninstall MUST
  run in `finally` — leaving gameinfo patched breaks the user's game on
  VAC servers.
- `record()` deletes each `<work>/N-sequence/` before recording: the work dir
  is per demo and the ffmpeg preset has no `-y`, so a stale `video.mp4` (from
  `--keep-clips` on an earlier run) would block the new recording and be
  returned as its result.
- Expected outputs per sequence: `<work>/N-sequence/video.mp4` +
  `take*/audio.wav` (use the last take folder).

## Capture defaults (sequences.py, LANChamp setup)

Set per clip in `build_actions_file`; they deviate from stock CSDM on purpose.

- **Audio:** game sound stays, CS2 music is muted (`AUDIO_COMMANDS`:
  `snd_musicvolume`, menu/round/MVP/objective/deathcam volumes at 0), and
  `snd_mute_losefocus 0` keeps sound when CS2 isn't the foreground window.
- **Crosshair:** one fixed green crosshair for every observed player
  (`CROSSHAIR_COMMANDS`, tuned for 1080p; the cvars follow CS2's September 2026
  rename — recheck them if a CS2 update changes crosshair behavior).
  `cl_show_observer_crosshair 0` is set alongside it.
- **Kill feed:** only the active POV player's kills appear
  (`mirv_deathmsg filter add attackerMatch=!x<steamid> block=1 lastRule=1`).
  No `localPlayer` highlight any more: the feed holds only the POV's kills. The filter is
  re-applied at setup and on every camera switch, together with
  `mirv_deathmsg clear`, so the feed never carries the previous POV's kills.
- **Kill-feed lifetime:** `max(60, clip seconds + 10)` with `lifetimeMod 1.0`,
  so POV kills persist to the end of the clip instead of fading after 5 s.
  `recording.death_notices_duration` is therefore no longer used here.
- **Extra commands:** `recording.extra_commands` (list of console command strings) and `--cmd` (repeatable)
  are emitted verbatim at each sequence's setup tick (`extra_console_commands`); no validation, so the
  commands are the user's responsibility (e.g. depth-of-field settings).
- **Kill-feed part colors:** `killfeed_background_color`, `killfeed_background_local_color`, `killfeed_border_color` (`"#RRGGBB[AA]"` or `"R G B A"`) override `killfeed_plain` per part.
- **Kill-feed plain:** `recording.killfeed_plain` (default true) sets `mirv_deathmsg colors background|backgroundLocal|border 0 0 0 0` (no row background, no red local-player border); not yet validated in-game.
- **Kill-feed colors:** `recording.killfeed_ct_color` / `killfeed_t_color` (`"#RRGGBB"` or
  `"#RRGGBBAA"`, default `""` = game colors) emit `mirv_deathmsg colors ct|t R G B A` at each
  sequence's setup tick (`killfeed_color_commands`; invalid values warn and are skipped).
  Per the HLAE wiki; whether AfxHookSource2 applies them is NOT yet validated in-game.
- **Name overrides:** `recording.player_names` (SteamID64 → name, TOML table
  `[recording.player_names]`) emits `mirv_replace_name byXuid add x<id> "<name>"`
  at each sequence's setup tick (`--player-name STEAMID=NAME`, repeatable, merges
  over the table). Empty by default = no commands. HLAE only renames
  "some parts of the HUD"; whether the kill feed is among them is NOT yet
  validated in-game.
- Every sequence must have at least one camera (raises otherwise): the first
  camera's player seeds the kill-feed filter.

## Cinematic cam paths (`--cinematic`)

- Cam paths are third-party/purchased and MUST NEVER be committed or copied
  into the repo (docs, tests, fixtures). They live in the gitignored
  `campaths/<Map>/` folder (`campaths/*`, `*.dz`, `*.campath` are ignored;
  only `campaths/README.md` is tracked) or wherever `paths.campaths_dir`
  points. Tests generate their own tiny campath XML.
- Flow: `cli.run` builds spatial metadata once → `recording/cinematic.py`
  `add_cinematic_sequences` appends, after each POV sequence, `--cinematic-takes N`
  copies over the same ticks with `Sequence.campath` set (renumbered 1..N,
  `cinematic_of` = the POV sequence's new number; spatial data re-keyed) →
  `write_report` is given that precomputed spatial dict.
- Kill feed: cinematic sequences block every death notice
  (`mirv_deathmsg filter add attackerMatch=!x0 block=1 lastRule=1`, re-applied
  on camera switches like the POV filter; bots have xuid 0 so their kills would
  still show). POV sequences are unchanged.
- `--cinematic-mode splice`: both takes cover identical ticks, so they align
  frame for frame. `recording/splice_plan.py` plans, from data only (so it is
  in the `--json-only` report as `cinematic.splice_segments`), the clip-second
  ranges where the cinematic replaces POV: samples at 10 Hz, POV kept ±1 s
  around each kill (the padding between two kills shrinks, floor 0.4 s, so a
  1.5 s cut still fits in a tight gap), the POV player (via `cameras` + `pov_trajectories`) must sit
  inside the cam path's view cone (horizontal fov, 0.85 margin, 16:9, ≤ 4000
  units; roll ignored; NO wall test, but a camera more than 30 units below the
  player's feet never sees them: `CAM_BELOW_TOLERANCE`, and with `floors` (floors.py) a target more than
  `LEVEL_GAP` below the floor under the camera is on another level), runs < 1.5 s dropped. Path choice is
  proximity-first (height difference weighted ×2, `VERTICAL_WEIGHT`, so paths on another
  floor rank behind same-floor ones) (same ranking as append mode): the closest path whose coverage
  is ≥ `MIN_COVERAGE` (= `MIN_SEGMENT`, 1.5 s: at least one cut) wins — coverage is only a gate, NOT the ranking (an
  earlier longest-coverage ranking picked far wide cameras that "see" the player
  through walls). Plays with no eligible path get no cinematic. Candidates
  (distance + coverage) are logged and stored in `cinematic.candidates`;
  `--cinematic-path NAME` forces one path for every play. `video/splice.py` overlays the cinematic with
  `overlay=enable='between(t,a,b)+…'` (re-encodes, POV audio kept) and
  `cli._splice_cinematics` swaps it in for the POV clip, so cinematic clips never
  reach the concat and final clip lengths equal POV lengths (kill times and
  `beat_targets` stay valid). Camera pose: quaternion wins over Euler
  (`qw/qx/qy/qz` vs `rx/ry/rz`); positive pitch looks down, positive yaw turns
  toward +y; verified that both encodings agree on real pack files.
- `--campath-hold` / `--campath-align` (`cinematic._plan_path`, `campaths.retime` / `write_retimed`): a
  sequence's `campath_retime` ({offset_seconds, hold_until_seconds}) makes `build_actions_file` write a
  shifted/extended copy of the path to `<work>/campaths/<stem>_<start_tick>.campath` and load that
  (first pose held until the offset, last pose held to the clip end; original only read, planning uses
  the same `retime` so it matches). Align tries offsets 0, 0.5 s, ... and keeps the longest splice
  coverage (smallest on ties); it needs splice mode. Plans are computed lazily: the first 3 candidates
  plus the chosen path. Unvalidated in-game (HLAE behaviour with duplicated keyframes).
- Commands (`sequences.py`): at `start_tick`, `mirv_campath enabled 1`,
  `load "<abs forward-slash path>"`, `edit start` (no args = rebase the path to
  the current demo time, as observer-pack binds do), then at `end_tick`
  `enabled 0`. When any sequence is cinematic, every sequence's setup also
  runs `enabled 0` + `clear` so a path never leaks into the next clip. Plain
  runs emit no campath commands — keep it that way.
- **Not yet validated in-game:** that campath coordinates equal demo world
  coordinates (used to rank paths by distance to the kills), that `edit start`
  works from the actions file, what the view does after a shorter path ends,
  that the paths' `fov` is horizontal (splice visibility assumes it), that
  `attackerMatch=!x0` blocks the whole kill feed, and that the POV and
  cinematic recordings really are frame-aligned (splice depends on it).

## Environment gotchas (this machine)

- `python` is **Microsoft Store Python**: MSIX virtualization makes its
  AppData/%TEMP% writes invisible to external processes (HLAE, CS2, FFmpeg).
  That's why app data lives in `~/.cs2-highlights` — never move it back to
  AppData, never default work dirs to %TEMP%.
- **HLAE vs CS2 build compatibility is the recurring blocker**: a CS2 update
  breaks injection (game dies ~2 s in with an `*_accessviolation.mdmp` next to
  cs2.exe — crashes go to Valve breakpad, NOT Windows event log). `doctor` has
  an "HLAE vs CS2 build" date check; fix = `update-tools` once advancedfx
  releases. It blocked recording in June 2026 (CS2 1.41.6.5 vs HLAE v2.190.2)
  and has since been resolved: end-to-end recording works on this machine.
  After any CS2 update, run `doctor` first and `update-tools` if it flags the
  HLAE date.
- Refreshing `vendor/cs2-plugin/server.dll`: extract
  `resources/static/cs2/server*.dll` from the latest CSDM Windows installer.
  py7zr cannot decode it (BCJ2 filter) — carve the 7z payload at the
  signature offset and use standalone `7zr.exe`. Script kept locally in
  `.research/extract_plugin.py`.
- Local test demo: `C:\Users\OS\Downloads\inferno.dem` (de_inferno, 21 rounds,
  tickrate 64). Known-good PoC clip:
  `record-one ... --start-tick 45771 --end-tick 47404 --steamid 76561198152383695`
  (Auttnic 4K, round 5).

## Verifying changes

1. `pytest` — detector/actions-file logic (fixtures built in `tests/conftest.py`).
2. `--json-only` on the local demo — the known highlights must appear
   (Auttnic 4K R5 top-scored or near; clutches in R6/R11/R13/R20).
3. Recording changes: `record-one` PoC first, watch the clip (correct POV,
   sound present), then a full run. Afterwards assert the CS2 folder is
   pristine: no `csdm/` dir, no `gameinfo.gi.backup`, no `<demo>.json` left.
