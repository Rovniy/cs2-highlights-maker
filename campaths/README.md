# Cinematic cam paths

Put your own HLAE cam path files here, one folder per map:

```
campaths/
  Nuke/      (or de_nuke/)
    A4.dz
    T1.dz
  Inferno/
    ...
```

Everything in this folder except this README is gitignored, so purchased or
otherwise licensed paths never reach the repository. Do not commit them and do
not copy them anywhere else in the repo (tests use generated paths only).

This folder is only the default. `campaths_dir` under `[paths]` in the config can point
elsewhere, or list several folders that are all read (the earlier one wins a name clash):
`campaths_dir = ["~/code/cs2-campaths", "~/campaths-premium"]`.

## Labels (optional)

Add a `labels.toml` next to a map's cam path files to give paths human-readable
names in the candidate list:

```
campaths/
  Nuke/
    sweep1.dz
    labels.toml
```

```toml
# labels.toml — keys are cam path file names without the extension
sweep1 = "Catwalk to A site sweep"
```

The label is shown next to the path name in the `--cinematic` candidate list.
Routes (the places each path passes through) are shown too, but only when a
place map exists — without `highlights places build`, routes are not shown.

## Static cams (optional)

Fixed camera positions for `--static-cam` go in `static.toml` (paste the `getpos` or `getpos_exact`
line from free cam) and/or a `static/` folder of cam path files (first point used, one point is
enough; `mirv_campath clear`, `mirv_campath add`, `mirv_campath save "<file>"`). The cam path is
HLAE's own camera pose; which `getpos` variant matches it in demo playback is not yet checked:

```
campaths/
  Nuke/
    static.toml
    static/
      outside-window.campath
```

```toml
[a-site]
getpos = "setpos -1234.5 567.8 128.0;setang 12.5 90.0 0.0"
label = "A site from heaven"   # optional; fov = 90 optional too
```

Used by `highlights <demo> --cinematic` and `--static-cam`. See the top-level README.
