# cs2-highlights

Generate highlight videos from Counter-Strike 2 demos: feed it a match `.dem`
file, get a `*-highlights.mp4` with the best moments rendered as real
in-game footage.

```
highlights match.dem                      # best moments of the whole match
highlights match.dem --player s1mple      # one player's highlights only
highlights match.dem --players s1mple,device   # several players (comma-separated or repeated)
highlights match.dem --json-only          # analysis only, no recording
highlights doctor                         # environment check
highlights update-tools                   # re-download latest HLAE/FFmpeg
```

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
rounds, overtime, final round). The top-N moments by score become clips;
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

[selection]
top = 10
min_kills = 2
```

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
