# Video perception + automation (MCP)

Eleven tools that make agent editing cheap: numbers first, one image when a picture is really
needed, and a whole timeline assembled by a single deterministic call from a text plan.

## Perception (no images unless asked)

| Tool | Returns | Cost |
|---|---|---|
| `probe(path)` | duration, fps, size, codec, audio layout, loudness | text ~80 tok |
| `index_media(paths, step_s)` | duration, scene cuts, luminance/colour/motion profile, loudness, speech runs; cached per file by size+mtime | text ~150 tok per minute, 0 on repeat |
| `audio_index(path, window_s)` | loudness windows, silences, speech-likeness runs | text |
| `transcribe(path, size, allow_download)` | timed speech segments via local whisper.cpp | text |
| `contact_sheet(path, start, end, cols, rows)` | one PNG grid of 16–64 moments + the time map | 1 image |
| `timeline_report()` | current timeline: shots, source frames, audio tracks | text |
| `timeline_storyboard()` | one PNG grid of the current timeline, one frame per shot | 1 image |

## Automation (one call, no image round-trips)

| Tool | What it does |
|---|---|
| `sfx_tags()` | lists semantic tags (`knife`, `sizzle`, `ignite`, `impact`, …) with default gain offsets |
| `list_recipes()` | the recipe menu: beat-locked section templates with their SFX |
| `apply_plan(plan, dry_run)` | builds a timeline from a text plan: shots, beat grid, SFX cues, camera bed, music |
| `apply_recipe(name, shots, …)` | same, but rhythm and sound come from a recipe; the caller supplies only clip + in-point per section |

### Plan format

```json
{
  "timeline": "Reel take 3",
  "fps": 50,
  "beat_grid": {"bpm": 110},
  "camera_bed": "/abs/path/camera.wav",
  "music": "/abs/path/track.wav",
  "shots": [{"clip": "ZHEL0500.mp4", "src_in": 100, "beats": 2}],
  "sfx": [{"frame": 0, "tag": "sizzle", "dur_s": 1.2}]
}
```

- `frames` or `beats`: with a `beat_grid`, beats are converted to frames; without one, give frames.
- `sfx[].frame` is absolute on the new timeline; `tag` resolves to a library file; repeated tags
  rotate through the tag's files so six knife hits are not the same sample.
- `dry_run: true` validates and returns the resolved shot list, cue names and total length without
  touching Resolve.
- Gains are computed against the bed: cue RMS = bed RMS + tag offset, peak capped at −4.4 dBFS.
  `bed_db` overrides the default bed loudness (−29 dBFS, the phone footage ambience).

Examples: `examples/plan.example.json`, `examples/recipe.example.json`.

### Recipes

`recipes/*.json` hold the section rhythm and the SFX pattern. `reel_frying` covers heat-driven
food reels, `reel_assembly` covers plating. Adding a recipe is adding a JSON file — no code.

## Layout

- `server/server.py` — MCP wiring and the perception tools.
- `server/sfx_library.py` — semantic tags over `sound-library/catalog.csv`.
- `server/audio_index.py` — loudness/silence/speech analysis and whisper.cpp ASR.
- `server/planner.py` — plan validation, cue baking, Resolve execution.
- `server/recipes.py` — recipe loading and expansion.
- `server/.venv` — Python 3.12 with `mcp<2` and `numpy`; ffmpeg/ffprobe come from `PATH`.
- `recipes/`, `examples/` — templates and worked examples.

## Caching

`index_media` keys each file by path+size+mtime+step and stores JSON in `~/.cache/percept/files/`;
a repeat call decodes nothing. Baked SFX cues are keyed by file+gain+duration in
`~/.cache/percept/sfx/`, so `apply_plan` reuses them across timelines.

## Limits

- Contact sheets carry no burned-in numbers; the caption maps tile → time in row-major order.
- Scene detection is a heuristic: a single continuous take reports `cuts=0`.
- `timeline_*` need Resolve open; `apply_*` also need the clips in the media pool.
- ASR needs `whisper-cli` and a model; the first `transcribe` with `allow_download` fetches
  `ggml-tiny.bin` (~75 MB) into `~/.cache/percept/models/`.
- Editing the server code needs the process restarted; killing it makes mcp-client relaunch it.
