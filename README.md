# cs2-highlights

Generate highlight videos from Counter-Strike 2 demos: feed it a match `.dem`
file, get a `*-highlights.mp4` with the best moments rendered as real
in-game footage.

```
highlights match.dem                      # best moments of the whole match
highlights match.dem --player s1mple      # one player's highlights only
highlights match.dem --players s1mple,device   # several players (comma-separated or repeated)
highlights match.dem --json-only          # analysis only, no recording
highlights match.dem --cinematic          # also re-record each play through your cam paths
highlights match.dem --cinematic --cinematic-mode splice   # cut the cinematic into the POV during down time
highlights match.dem --intro-cam face     # open each clip with a generated face cam that eases into the POV
highlights match.dem --downtime-cam random   # cut generated cams into down time between kills
highlights match.dem --vertical blur --vertical-killfeed --max-duration 55   # also a 1080x1920 Shorts/Reels version
highlights match.dem --slow-mode auto     # slow-motion beat on the clips whose kill is most worth it
highlights doctor                         # environment check
highlights update-tools                   # re-download latest HLAE/FFmpeg
```

### Compilations across demos

Rank the best moments over several demos at once:

```
highlights top a.dem b.dem c.dem --top 10
highlights top *.dem --players Alice --players Bob
highlights top *.dem --players Alice,Bob --max-duration 247   # best clips that fit 4m07s in total
```

This only prints a ranked table (no JSON report, no recording). `--max-duration SECONDS` keeps the best
clips whose lengths add up to at most that total (a clip that would overrun is skipped, shorter ones can still fit).
Each row shows the clip's estimated Length (lead-in and lead-out included) and the output ends with
`Total: 2m 29s`: handy for picking a song, but rough, since slow-mo, trims and transitions in the edit change it.

### One clip per highlight across demos (`batch`)

For a separate compilation step (for example lanchamp-cotw), `highlights batch` turns the best
moments of several demos into individual files:

```
highlights batch season59-match07=a.dem season59-match08=b.dem \
  --players "FreeDotWilly,FuzzyHat,GUNK,NoSwear" --top 15 \
  --cinematic --campath-align --intro-cam random --downtime-cam random --fps 120 -o out/
```

- `DEMO` is `PATH`, `LABEL=PATH` or a folder (`--recursive` adds its sub-folders, like `top`); the
  label names the files (default: the file name).
- `--top N` is global: the N best moments of all demos together, optionally only for `--players`
  (or `--team Saltcrew --names-file names.json`, the lanchamp-cotw file, to take a team's players).
  Players missing from a demo are fine; a demo with none of them is skipped. With no filter
  everyone is eligible. `--max-duration` also works.
- Each moment becomes `<label>-<player>-<n>.mp4` plus a `.json` in `-o` (default `highlights-out/`):
  `n` counts that player's picked clips in that demo, best first. The JSON is the same report a single-clip `run` writes (sequence 1, `beat_targets`, `cinematic.*`),
  plus `batch`: `{"rank": 1, "of": 35, "score": 182.4, "relative_score": 1.0}` (rank 1 = best of the
  whole batch, relative_score = score / best score) so an editing tool can order the clips. There is
  no combined video.
- Each moment is its own isolated clip (never merged with another), with its cinematic cam path
  (splice mode), generated intro/down-time cams, kill-feed settings and `--cmd` commands.
  Slow-motion is not part of it (leave it to the compilation step; use `--fps 120` for that).
- Selection is global but recording is one game session per demo. A demo that fails to record is
  skipped with a warning and the rest continue. `--json-only` prints the table and writes the
  JSONs without recording.
- `slowmo_suggestion` in each JSON is the kill most worth a slow-motion beat (the same pick as
  `--slow-mode auto`, best third of the clips): `kill_tick`, `clip_time_seconds`, the window
  (`start_seconds`/`end_seconds`), `speed`, `reason`, `"applied": false` (null when none). The
  table's "Slow-mo" column shows them.
- Kill times are measured from the recording: each new kill-feed row is a sudden change in the
  kill-feed corner, and when most of a clip's kills agree on one shift it becomes that clip's
  offset (`editing.video_timing.kill_sync`, status `calibrated_from_kill_feed`). Ambiguous clips keep
  the nominal times. `--no-kill-sync` turns it off. Not yet validated against real recordings.
- `--buildup empty|players` adds `<name>-buildup.mp4` + `.json` (`kind: buildup`, `for_clip`) per
  clip: a cam-path shot from the same round (freeze time included) that ends right before the
  play starts, with nobody / players in view. `--buildup-length` caps it (default 6 s).
- After recording, the cam-path parts of every clip are checked for clipping and contact sheets
  are made (see "Reviewing clips" below); `--no-review` skips it.
- `--resume` skips clips whose `.mp4` is already in the output folder. `--reuse-recordings` reuses
  raw recordings kept by an earlier `--keep-clips` run (and keeps them), recording only sequences
  that are missing or changed (ticks, cameras, cam path file, fps/size, `--cmd`, names), so a
  post-processing change does not need CS2 again.

### Place names

Teach the tool where plays happen on each map:

```
highlights places build a.dem b.dem      # samples alive players' place names
highlights places list                   # show saved maps and cell counts
```

Run `build` once per map on a few demos (use `--every SECONDS` to sample more
or less often); results accumulate in `~/.cs2-highlights/places/<map>.json`
and label where each play happened without parsing the demo again.
`highlights top FOLDER --recursive --build-places` does the same while ranking, but only for maps
that have no place map yet: every demo of such a map in the run is sampled (maps that already have
one are left alone; run `places build` to add more demos to them).
`--rebuild-places` replaces the saved place map of every map in the run with one built only from
these demos, for when a map update moves or adds areas (use demos played on the new version).

The highlights table has a "Where" column with each clip's route (the places
the POV player moved through), and every sequence in the JSON report carries
`spatial.route` — `{places, text, kills}` — listing that route and where each
featured kill happened.

## How it works

```
match.dem → [demoparser2 parsing] → events (kills, rounds, clutches)
          → [detector] → top-N moments scored by "watchability"
          → [recording: CS2 + HLAE] → clips (frames piped straight into FFmpeg)
          → [FFmpeg] → highlights.mp4
```

Rendering video from a demo without the game is impossible, so recording uses
the same mechanism as [CS Demo Manager](https://cs-demo-manager.com) (MIT):
the game is launched through [HLAE](https://www.advancedfx.org/) with an
`AfxHookSource2.dll` injection, the CSDM server plugin executes console
commands at exact demo ticks (driven by a `<demo>.json` actions file), and
`mirv_streams` pipes frames directly into FFmpeg with no raw TGA files.

## Requirements

- Windows, CS2 installed, Steam running
- Python ≥ 3.11
- HLAE and FFmpeg — downloaded automatically on first run
- `server.dll` (the CSDM CS2 plugin) — bundled in `vendor/cs2-plugin/`, or
  picked up from an installed CS Demo Manager (see `highlights doctor`)

## Installation

```powershell
python -m venv .venv
.venv\Scripts\pip install -e .[dev]
.venv\Scripts\highlights doctor
```

## What counts as a highlight

A player's kills within a round are clustered into "moments" (gap < 20 s),
and each moment gets a score: multikills (3K/4K/ACE), headshots, knife/zeus
kills, AWP noscopes, kills through smoke / while flashed / wallbangs, won 1vX
clutches (detected by tracking alive players), and round importance (pistol
rounds, overtime, final round, match point, and economy: a play by a team on an eco against a
full buy scores higher, one by the full-buy side against an eco lower). Match point and eco
show after the highlight in the tables, e.g. `3K (aug) · match point`. The top-N moments by score become clips;
overlapping moments are merged into a single clip with camera switches
between the featured players.

Scoring weights live in `src/highlights/analysis/scoring.py` (`WEIGHTS`).

## Configuration

`~\.cs2-highlights\config.toml` (everything can be overridden by CLI flags).
The folder is deliberately NOT under AppData: Microsoft Store Python
virtualizes AppData writes inside its MSIX package, making them invisible to
external processes (HLAE/CS2/FFmpeg).

```toml
[paths]
cs2_exe = "auto"      # auto: Steam registry + libraryfolders.vdf
hlae_dir = "auto"     # auto: downloaded into ~\.cs2-highlights\tools
ffmpeg_exe = "auto"
plugin_dll = "auto"   # auto: vendor/cs2-plugin, then an installed CSDM
work_dir = "auto"     # auto: ~\.cs2-highlights\work

[recording]
fps = 60
width = 1920
height = 1080
crf = 23
lead_in_seconds = 4.0
lead_out_seconds = 3.0
# Optional extra console commands run at every clip's start (e.g. depth of field / post-processing).
# The same per run with `--cmd "command"` (repeatable, added after these).
# Depth of field with CS2's own cvars (distances in game units; starting values, tune to taste):
# extra_commands = [
#   "r_dof_override 1",
#   "r_dof_override_near_blurry 20",
#   "r_dof_override_near_crisp 60",
#   "r_dof_override_far_crisp 300",
#   "r_dof_override_far_blurry 1200",
#   "r_dof_override_tilt_to_ground 0",
# ]
# Optional kill-feed team colors (#RRGGBB or #RRGGBBAA); leave out for the game's own.
killfeed_ct_color = "#00ACE7"
killfeed_t_color = "#F6CA26"
killfeed_plain = true   # default: transparent row backgrounds and no red local-player border
# Per-part colors ("#RRGGBB[AA]" or "R G B A"); override killfeed_plain for that part.
# killfeed_background_color = "0 0 0 0"
# killfeed_background_local_color = "0 0 0 0"
# killfeed_border_color = "255 0 0 255"

# Optional: show custom names (SteamID64 = "name") via HLAE's mirv_replace_name.
# HLAE only renames "some parts of the HUD"; check the kill feed in-game.
# The same can be done per run with `--player-name 76561198000000000="Display Name"`
# (repeatable; CLI overrides this table).
[recording.player_names]
76561198000000000 = "Display Name"

[selection]
top = 10
min_kills = 2

# FACEIT Data API server-side key (developers.faceit.com); FACEIT_API_KEY env var wins.
[faceit]
api_key = ""
```

## FACEIT match context (team, opponent, ESEA division)

When a demo's file name (or batch label) contains a FACEIT match room id, e.g.
`1-06f966d6-b4e4-4aab-9442-18de41412123-1-1.dem`, `run` and `batch` look the match up in the
FACEIT Data API and add a `match` block to every report JSON:

```json
"match": {
  "match_id": "1-06f966d6-...", "url": "https://www.faceit.com/en/cs2/room/1-06f966d6-...",
  "league": "ESEA", "competition": "S59 NA Open1-8 East - Regular Season",
  "season": 59, "region": "NA", "division": "ESEA Open 1-8", "group": "East", "stage": "Regular Season",
  "best_of": 1, "teams": ["Team A", "Team B"], "score": {"Team A": 9, "Team B": 13}, "winner": "Team B",
  "players": {"<steamid64>": {"nickname": "...", "team": "Team A", "opponent": "Team B"}}
}
```

Players are keyed by SteamID64 (the report's `moments[].steamid`). Stand-ins missing from the
FACEIT roster get the team their in-game side belongs to. The division is "<league> <division>"
parsed from the competition name for ESEA leagues ("ESEA Open 1-8", "ESEA Main"; they have no championship record in
the API), otherwise the competition name itself ("BHOP Playoffs A"). Finished matches are
cached in `~/.cs2-highlights/faceit/`. `highlights top` shows a Match column (from the row player's side; `top` also
takes folders, with `--recursive` for sub-folders). `highlights scan` (with or without `--recursive`) shows the
match per demo in a Match column and stores it (without the roster, plus the scanned player's
`team`/`opponent`) as `match` in its `.json`. Without an API key, for other file names, or when the API
fails, the block is left out and nothing else changes.

## Cinematic cam paths

`--cinematic` records every selected play a second time through an HLAE cam
path, so you can cut between the POV take and a cinematic take of the same
moment. The paths are **not included**: bring your own (for example a
purchased observer pack) and keep them out of git.

1. Put HLAE campath files (`.dz`, `.campath` or `.xml`) in `campaths/`, in one
   folder per map named like the map (`Nuke/` or `de_nuke/`):

   ```
   campaths/
     Nuke/
       A4.dz
       T1.dz
   ```

   `campaths/` is gitignored (apart from its README), as are `*.dz` and
   `*.campath` everywhere, so these files cannot be committed by accident.
   To use another location set `campaths_dir` under `[paths]` in the config. It also takes a
   list, e.g. a free library plus a paid pack; every folder's map folders are read, in order:

   ```toml
   [paths]
   campaths_dir = ["~/code/cs2-campaths", "~/campaths-premium"]   # "auto" = this repo's campaths/
   ```

   Names identify paths and static cams (`--cinematic-path`, `--static-cam`, `labels.toml`), so
   when two folders have the same name (case-insensitive) the earlier folder wins and a warning
   says which was skipped; rename one to use both. `highlights doctor` lists the folders.
2. Run `highlights match.dem --cinematic`. For each play the cam paths that
   pass closest to its kills are chosen; `--cinematic-takes 2` records the two
   best matches. Each cinematic clip follows its POV clip in the final video,
   and the JSON report marks it with `"cinematic": {"campath", "of_sequence"}`
   (its `beat_targets` entries carry `"cinematic": true`).
3. Cinematic clips show no kill feed (POV clips keep theirs).
4. `--cinematic-mode splice` instead of the default `append` produces one clip
   per play with the same length as the POV clip: the cinematic is cut in
   during down time between kills, only while its camera is pointed at the
   player. The POV footage is kept from 1 s before to 1 s after each kill
   (less when two kills are close together, so a cut still fits between
   them). Per play, the cam path that passes closest to the kills is used,
   provided it can cut in for at least 1.5 s, one minimum cut (`--cinematic-takes` is ignored);
   plays no path can cover stay POV-only. The planned cuts are in the JSON
   (`cinematic.splice_segments`, clip seconds), so `--json-only` previews
   them without recording. Because lengths are unchanged, kill times and
   `beat_targets` are unaffected.
5. The console (and, for append and splice takes alike, the JSON's
   `cinematic.candidates`) lists the closest candidate paths per play with
   their distance to the kills and, in splice mode, how long each could cut
   in. To force one path for every play, use `--cinematic-path B5` (the file
   name without extension).
6. `highlights doctor` shows how many cam paths were found.

Cam paths are often shorter than the play. Two opt-in flags change how a path is timed:

- `--campath-hold` keeps the path's last pose until the clip ends (instead of the view returning to
  the POV when the path finishes), so a locked-off final shot can cover the rest of the play.
- `--campath-align` (splice mode only, implies hold) also starts the path late: for each play it
  tries start offsets in 0.5 s steps (the first pose is held until then) and keeps the one that
  cuts in longest. The offset is in the JSON as `cinematic.campath_retime`
  (`offset_seconds`, `hold_until_seconds`).

Both write a retimed copy of the path into `<work_dir>/campaths/` (your files are only read, and
nothing from them goes into the repo); the held pose is a fixed camera, so it only cuts in while it
still sees the player. Neither is validated in-game yet.

Before recording anything, `highlights match.dem --suggest-cinematic --json-only`
previews per clip whether a cinematic is worth it, the best path, how long it
sees the player, its route, and alternatives. Then pick one with
`--cinematic --cinematic-path NAME`. The suggestions are also in the JSON
report under `cinematic_suggestion`.

Splice limits: visibility is a view-cone test against the player's position
and does not know about walls, so a cut can land on a camera that is pointed
at the player but blocked by geometry; review `splice_segments` before a long
run. The POV kill feed (which persists to the clip's end) disappears while a
cinematic cut is showing.

Notes: the path starts playing at the first recorded frame (`mirv_campath edit
start`). If it is shorter than the clip, the view returns to the POV player
when it ends, and a warning is printed. Matching assumes cam path coordinates
equal demo world coordinates, which has not been verified in-game yet.

## Generated cams (`--intro-cam`, `--downtime-cam`)

No cam path files needed: the tool computes a cam path from the demo that follows the POV
player and records it as an extra take, which is cut into the POV clip like a splice-mode
cinematic (clip length, kill times and audio are unchanged).

Styles: `face`, `profile`, `gun`, `tps` (over the shoulder, a wider shot), `shoulder` (broadcast over-the-shoulder: tight, about 40 units behind and just right of the head, looking where the player looks), `heli` (high, looking down).
Pass one, a comma-separated list, or `random` (picked per clip, the same way on every run
of the same demo). `gun` is a wide shot 1-2 m from the player; `face` is a tight close-up from the player's front-left (about 20-26 units from the head, the face fills most of the frame; it stays off the rifle, which points forward on their right).

- `--intro-cam STYLE` opens every clip (up to 3.5 s, shorter when a kill comes early) on
  the style's shot, then spirals round to just behind the player and pushes into the exact
  first-person eye pose, so the cut to the POV has no jump. With `--intro-transition cut` it
  stays on the chosen shot for the whole intro (slow drift only) and hard-cuts to the POV
  instead; `swoop` is the default. The report shows it as `transition` on the intro cut.
- `--downtime-cam STYLE` fills the long gaps between kills with one continuous pass of the
  style's shot (hard cut in and out, no swooping back to the POV). Only gaps of at least
  2.5 s count, each pass is up to 12 s, and at most the 2 longest gaps per clip are used, so
  a clip is not chopped up. Ranges a `--cinematic --cinematic-mode splice` cam path already
  covers are left to that path.
- A list or `random` picks one style per kind per clip (not per cut), so a clip never mixes
  cam styles.

Both can be combined with each other and with `--cinematic`; the JSON report lists the cuts
under `cinematic.generated_cams`. They are not checked against map geometry: a camera can
end up inside a wall (worst for `face`, `profile` and `tps` next to walls, and `heli`
indoors), so review each run. The offsets are estimates, tuned in
`src/highlights/recording/dyncams.py`. Not validated in-game yet: that the observed
player's model is drawn while a cam path is active, the cam path fov value, and the
hand-off to the POV.

## X-ray on wallbangs and smoke kills (`--xray auto|all`)

`run` and `batch` can dissolve an x-ray view (players visible through walls) over the play around kills
that went through a wall or through smoke, then dissolve back, so it never just pops on and off.

```powershell
highlights batch final.dem --top 10 --xray auto -o clips
highlights run match.dem --xray auto --xray-fade 0.6
```

- **Which kills:** `auto` = a bullet that went through a wall (`penetrated`, minus any other players
  standing on the shot line, so a shot through a player only does not count) or through smoke. Plays with
  none get no x-ray take at all, so only the clips that need it pay for a second recording. `all` = every
  selected kill of every play (handy for trying the look).
- **How:** the whole play is recorded a second time with `spec_show_xray 1` (the normal take keeps it at 0),
  frame-aligned with the normal take. The splice step cuts it in around each qualifying kill: full strength
  from 1 s before to 0.5 s after the kill, with a dissolve of `--xray-fade` seconds (default 0.4) outside
  that. Kills close enough for their dissolves to overlap share one window. The clip keeps its length,
  audio and kill times. A cam-path or generated-cam cut wins where it overlaps an x-ray window.
- **JSON:** each report sequence has an `xray` key (windows, fade lengths, kill ticks, reasons); the x-ray
  take is its own sequence with `xray.of_sequence` pointing at the play, like cinematic takes.
- **Not validated in-game:** that `spec_show_xray 1` draws anything in a recording, and that it applies in
  first person. `highlights check-ingame DEMO` now records an x-ray take (`05-xray.mp4`) and the checklist
  says what to look for; run it before trusting `--xray`. The player-on-the-line test and the 1 s / 0.5 s
  windows are estimates, not tuned on real demos. Not combinable with `--suggest-cinematic` or
  `--cinematic-only`.

## Static cams (`--static-cam auto|all|NAME[,NAME]`)

A fixed camera over the play (a site where a retake develops, the exact spot where the enemy dies),
recorded over the same ticks as the POV clip and written as its own file for editing by hand. It is
never spliced into the POV clip or the highlights video.

```
highlights match.dem --static-cam auto                 # match.dem-highlights-clip01-static-b-site.mp4, ...
highlights batch a.dem b.dem --top 10 -o out --static-cam all   # out/<name>-static-<cam>.mp4 next to <name>.mp4
highlights match.dem --static-cam b-site,a-long        # these cams for every play
```

- `auto`: per play, the cam that sees the most of the play's deaths (victim position at the kill), then
  the most time with the POV player in view, then the closest. `all`: every cam that sees at least one
  death or the player for 1.5 s. Names: those cams for every play (warns when one sees nothing).
- Define cams per map next to the cam paths, in `campaths/<Map>/static.toml`. In a demo, switch to free
  cam, frame the shot and type `getpos` in the console; paste the line it prints:

  ```toml
  [b-site]
  getpos = "setpos 1000 -350 330;setang 18 90 0"
  fov = 90                              # optional (default 90)
  label = "B site from the entrance"    # optional

  [a-long]
  pos = [-1765, 250, 350]               # or position and angles separately
  angles = [20, 90]                     # pitch (positive looks down), yaw
  ```

  Or drop HLAE cam path files in `campaths/<Map>/static/`: the first point is used, so a path with a
  single point (`mirv_campath add` once, then save) is enough. HLAE itself cannot play a path that short
  (its default cubic interpolation needs 4 points), but the file is only read for its pose: the take
  plays a generated 4-point hold.
- The report lists each take under `cinematic.static_cam` (deaths seen, seconds of the player in view,
  distance). The kill feed is hidden in static takes, like in cam-path takes.
- `getpos_exact` lines (`setpos_exact ...;setang_exact ...`) are read too. In demo playback the two
  can print positions over 1,000 units apart with the same angles, so at most one of them is the free
  cam. Until that is checked, the one-point cam path is the safe capture: it is HLAE's own camera pose,
  the same thing playback uses (`mirv_campath print` shows it next to `getpos`/`getpos_exact`).
- Floors: a cam never "sees" a level below its own (Nuke: a cam over A does not see B site through
  the floor). Floor heights come from where players stood in the demo plus the saved place map
  (`highlights places build`, which helps for areas the demo never visits, e.g. a wingman match);
  anything more than 200 units below the floor under the cam counts as another level. The same check
  applies to `--cinematic` splice matching, `--suggest-cinematic` and b-roll.
- Not validated in-game yet: same view-cone check as splice mode (walls on the cam's own level are
  not checked), and which of `getpos`/`getpos_exact` gives the free-cam position.

## Static cams in the down time (`--static-downtime auto|pov|enemies`)

Cuts your static cams (the same `static.toml` / `static/` cams as `--static-cam`) into the clip itself,
in the down time between kills: the rotation to the next fight, or the other team setting up.

```
highlights match.dem --static-downtime auto
highlights batch DEMOS... --top 20 -o out --static-downtime enemies
```

- The down time is the same as for `--downtime-cam`: the gaps between the POV kept around each kill
  (1 s either side), split at camera switches, at least 2.5 s, at most 12 s, the two longest per clip,
  minus anything a `--cinematic` cam path already cuts in.
- Per stretch, every static cam is judged (sampled every 0.5 s, with the view cone and floor check):
  - `pov`: the cam with the POV player in view longest (at least half the stretch).
  - `enemies`: the cam with the most living enemies in view on average (at least one enemy in view
    for half the stretch).
  - `auto`: `enemies` when that cam sees 2 or more enemies on average, else `pov`, else `enemies`.
  A stretch no cam qualifies for stays POV.
- Each winning cam is recorded once over the whole clip (a fixed hold, kill feed hidden) and cut into
  the POV clip for the stretches it won, like the generated cams; `<name>.mp4` keeps POV length and kill
  times. Order on top of each other: cam path, static cams, generated/grenade cams.
- The report lists the cuts under `cinematic.static_downtime` (cam, what it shows, player share,
  enemies seen).
- Not combinable with `--downtime-cam` (both fill the down time) or `--suggest-cinematic`; `--static-cam`
  works alongside it (separate files).
- Same limits as the other cams: no wall check on the cam's own level, unvalidated in-game.

## Grenade cam (`--grenade-cam auto|all`)

On `run` and `batch`, a camera rides just behind a grenade the POV player throws, from the moment
it leaves the hand until it goes off, then holds on the explosion, flash or fire for a moment
before cutting back to first person. It is cut into the clip like the generated cams (on top of
them), so the clip keeps its length and kill times.

- `auto` follows only grenades that matter: an HE or molotov that kills, or that damages two or
  more enemies, and a flash that blinds an enemy (1 s or more) who dies within 3 s. `all` follows
  every HE, flash and molotov the POV player throws in the clip. Smokes and decoys are never followed.
- The report lists each one under `cinematic.generated_cams` (`kind: grenade`, the grenade type and
  why it was picked, e.g. `HE kill`, `flash assist`, `molotov hits 2`).
- Reading the grenade flights takes a few seconds and a few GB of memory per demo, so it only
  happens with this option.
- Camera distance and height are estimates in `src/highlights/recording/grenade_cam.py` (`FOLLOW`,
  `RISE`, `LOOK_AHEAD`, `HOLD`). There is no wall check: a grenade bouncing off a wall can pull the
  camera into it; the review after `batch` flags dark or featureless frames. Not validated in-game.

## Slow-motion (`--slow-mode auto`)

`highlights top a.dem b.dem --slow-mode auto` adds a "Slow-mo" column showing which clips would
get a slow-motion beat and on which kill (for example `kill 4/4: 4K finish`); `run` uses the same
decision and records it in the JSON as `slowmo` per sequence (`--json-only` previews it).

- A kill qualifies as the last kill of a 4K or ace, the last kill of a won clutch, or a knife,
  zeus, AWP noscope, blind, through-smoke or wallbang kill (plain kills and headshots do not
  qualify on their own). Each clip gets at most one beat, on its best kill.
- Only the best third of the clips (rounded up) are slowed, so it stays special; with nothing
  qualifying nothing is slowed.
- The beat runs 0.7 s before to 1.1 s after the kill at 0.4x, retimed with FFmpeg on the finished
  clip (audio slowed too). Cinematic takes are cut into their POV clip first, so they are
  unaffected. The clip gets about 2.7 s longer; `--max-duration` counts that (the lowest-worth
  beats are dropped first when it would not fit), and the report's `video_time_seconds` and
  `beat_targets` include the retime.

## Sorting a folder of demos (`highlights scan`)

```powershell
highlights scan "D:\demos\2026" --players "FuzzyHat" --min-score 8 --recursive
```

Parses every `.dem` in the folder and sorts it: **keep** when one of the players has a play scoring at least
`--min-score` (the Score column of `top`; run `top` on a few demos first to see what 8 or 12 looks like),
**remove** when their best play is lower or they are not in the demo, **unreadable** when it cannot be parsed.
It prints a table (best play, round, score per demo) and writes `<folder>\demo-scan.txt` (paths only, under
`# KEEP` / `# REMOVE` headings) plus `demo-scan.json` with the details. Nothing is deleted unless you add `--delete`: it lists the `remove` demos, asks for confirmation
(`--yes` skips the question) and then permanently deletes only those (never `keep` or `unreadable`). Run without it
first and read the list. `-o` picks another file.

## Cinematic-only teaser clips (`batch --cinematic-only`)

```powershell
highlights batch a.dem b.dem --players "FuzzyHat" --top 12 --cinematic-only -o teaser
```

Cam-path footage only, for a teaser/trailer over a slow song: no POV, no audio, no kill feed. Per play it finds the
stretch where one of your cam paths sees the player (kills included, unlike `--cinematic` which keeps the POV around
them), keeps up to `--clip-length` seconds of it (default 5, centred on the kills), records at `--fps` (default 240)
and by default keeps it at full speed (`--speed 1`: a 240 fps clip, so your own slow-motion step has real frames to
work with). `--speed 0.25` slows it 4x here instead: 240 fps at 0.25x is a 60 fps clip of real frames, no
interpolation. `--output-fps` overrides the output rate. Plays no cam path can see are skipped with a warning.
Everything that picks plays works as in `batch` (`--players`, `--team`, `--round`, `--top`, `--pick`/`--exclude`,
`--cinematic-path`, `--campath-hold`/`--campath-align`, `--resume`, `--reuse-recordings`). Output is
`<label>-<player>-<n>.mp4` plus a small `.json` (`kind: teaser`: window, speed, fps, kill times in the slowed clip).
Needs cam paths in `campaths/<Map>/`; not combinable with the generated cams, grenade cam, build-ups or tilt-shift.
Recording at 240 fps is slow, so try a couple of plays first (`--top 2`). Unvalidated in-game.

## Picking plays by round (`--round`)

`run`, `top` and `batch` take `--round` to keep only plays from certain rounds, using the round
numbers shown in the table. It is applied before ranking, so `--top` counts only those plays.

```powershell
highlights match.dem --player deviN --round 13 --cinematic --cinematic-mode splice
```

```powershell
highlights batch semi=a.dem final=b.dem --players "A,B" --round semi:1,2,10 --round final:6,7,13,15 -o out
```

- `13` or `13,15`: those rounds in every demo. `semi:2,10`: those rounds in the demo labelled
  `semi` only (the label is the `LABEL=` of `batch`, or the file name without `.dem` for `top`);
  `run` has one demo, so plain numbers are all it needs.
- It can be repeated and combined with `--players`, which is how you separate two players who
  both had a play in the same round.

## Picking rows from the table (`--pick`, `--exclude`)

`top` and `batch` take `--pick 1,2,4` (or ranges like `3-6`) to keep only those rows of the table
the same command prints without them, and `--exclude 3,7` to drop rows. Run `top` once, then give
`batch` the same options plus `--pick`; the table keeps the original numbers.

```powershell
highlights top a.dem b.dem --players "A,B,C" --top 10
highlights batch a.dem b.dem --players "A,B,C" --top 10 --pick 1,2,4,5,7 --cinematic -o out
```

## Reviewing clips (`highlights review`)

`batch` and `broll` end with a review of what they wrote, and `highlights review DIR` runs it on
any folder of clips with their `.json` files:

- `review/<name>.jpg`: a contact sheet, 12 frames spread over the clip.
- `review/index.html`: every clip's sheet and verdict on one page, linking to the clip.
- A clipping check on the cam-path parts only (cinematic and generated-cam segments, whole B-roll
  and build-up clips; never first-person footage): runs of near-black or featureless frames are
  flagged in the log, the page and the JSON (`review.suspect_ranges`). It is a heuristic: it can
  miss a camera that sees through a wall and can flag a plain shot that is fine.

## Checking the in-game assumptions (`highlights check-ingame`)

Several behaviours can only be confirmed by watching a recording (kill-feed colours and filter,
name replacement, `--cmd` commands, cam-path coordinates, held poses, the face intro, splice
alignment). `highlights check-ingame DEMO [--player NAME] [--cmd ...] [--player-name ...]` records
the best play as `01-pov.mp4`, `02-campath.mp4`, `03-face-intro.mp4` and `04-spliced.mp4` in
`ingame-check/`, with `CHECKLIST.md` listing exactly what each clip should show and when.

## B-roll for pre-edit build-up (`highlights broll`)

Records your cam paths on their own, with no highlight, so you have footage to open a clip with.
Each clip is one cam path over a random stretch of live play (after freeze time), up to
`--max-length` seconds (default 8):

```powershell
highlights broll season59-match07=C:\demos\match07.dem --mode empty   --count 4 -o broll
highlights broll season59-match07=C:\demos\match07.dem --mode players --count 4 -o broll
```

- `--mode empty`: only windows where no living player is in view (establishing shots).
- `--mode players`: only windows where at least one living player is in view for 60% or more of
  the clip, so you get random people from that demo. The JSON lists who was in view.
- Output: `<label>-broll-<mode>-<n>.mp4` plus a small `.json` (`"kind": "broll"`, round, cam path,
  ticks, players in view) per clip. `--count` is per demo; several demos are recorded in turn.
- `--seed N` repeats a random pick, `--cinematic-path NAME` restricts it to one cam path,
  `--json-only` plans and writes the JSON without launching CS2 (the log lists each pick).
- Visibility is a view-cone test on positions with a floor check (a player on a level below the
  cam is not in view) but no wall check: a player behind a wall on the cam's level counts as in
  view. That errs on the safe side for `empty` and can include a hidden player in `players`.

## Tilt-shift (`--tilt-mode auto|on`)

Gives cam-path shots a miniature "tilt-shift" look: the middle band of the frame stays sharp,
the top and bottom blur out, and colour gets a slight boost. It needs `--cinematic
--cinematic-mode splice` (`batch` is always splice) and applies only to the cam-path footage cut
into a clip, never to first-person footage or the generated intro/down-time cams (those are
close-ups, where it just looks out of focus).

- `--tilt-mode on` tilts every cam-path shot.
- `--tilt-mode auto` tilts only wide, elevated shots: on average at least 700 units from the
  player and 150 units above them while it is on screen, best first, and at most half of the
  plays. Close or ground-level cam paths stay sharp.
- The decision is data-only, so `--json-only` previews it (`tilt` per sequence in the JSON, with
  the distance and height behind it). The look itself is an FFmpeg filter applied to the cam-path
  footage before it is overlaid (`src/highlights/video/tiltshift.py` has the blur, band and
  saturation constants).

```powershell
highlights match.dem --cinematic --cinematic-mode splice --tilt-mode auto
```

## Vertical version (`--vertical`)

Recording stays 16:9; `--vertical blur|crop` adds a 1080x1920 copy of the finished video next
to it (`<output>-vertical.mp4`, audio copied, timings unchanged).

- `blur`: the footage fills the width (1080x608) over a blurred, zoomed copy of itself, so
  nothing is cropped. `--vertical-killfeed` also cuts the top-right kill feed out and shows it
  enlarged below the footage (assumes the default kill-feed position).
- `crop`: the centre 9:16 slice, scaled up; loses the kill feed and the sides.
- Use `--max-duration 55` to keep the whole video inside a Shorts-style limit.

## Important notes

- The game is launched strictly with `-insecure` (the plugin enforces this
  too). **Never join VAC-secured servers while HLAE is running.**
- Recording runs at roughly real time: 10 clips of ~20 s each ≈ 10–15 minutes
  (game boot + demo seeks + the recording itself).
- CS2 updates regularly break HLAE injection until advancedfx ships a new
  release (usually within days). `highlights doctor` compares the CS2 build
  date against your HLAE release and tells you when to run
  `highlights update-tools`.
- FACEIT demos arrive as `.gz`, Valve ones sometimes as `.dem.zst` — both are
  decompressed automatically. CS:GO-era demos are not supported (CS2 cannot
  play them).
- Your game config is untouched: the game runs with a sandboxed settings
  folder via the `USRLOCALCSGO` environment variable.

## Attribution

The recording mechanics are ported from the
[akiver/cs-demo-manager](https://github.com/akiver/cs-demo-manager) sources
(MIT). `vendor/cs2-plugin/server.dll` is the CSDM CS2 plugin binary (MIT, see
`vendor/cs2-plugin/NOTICE`).
