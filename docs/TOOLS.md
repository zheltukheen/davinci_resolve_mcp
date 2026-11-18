# Tool reference

Two servers, 42 tools. `resolve` controls the app; `percept` decides and assembles.

## resolve (14 tools)

JSON in, JSON out. Every result is an object: `{"ok": true, "action": ...}` on success or
`{"ok": false, "error": "no_project"|"not_found"|"invalid_argument"|"failed"|"no_resolve",
"message": "..."}` on failure, so a chain can branch on codes instead of parsing prose. The original
prose server is kept as `server_legacy.py`.

| Tool | Arguments | Returns |
|---|---|---|
| `create_project` | `name` | success/failure sentence |
| `load_project` | `name` | success/failure sentence |
| `save_project` | — | success/failure sentence |
| `create_timeline` | `name` | success/failure sentence |
| `set_current_timeline` | `index` | success/failure sentence |
| `create_timeline_from_clips` | `name`, `clip_indices` | success/failure sentence |
| `import_media` | `file_paths[]` | success/failure sentence |
| `create_folder` | `name` | success/failure sentence |
| `add_fusion_comp_to_clip` | `timeline_index`, `track_type`, `track_index`, `item_index` | success/failure sentence |
| `create_fusion_node` | `node_type`, `parameters` | success/failure sentence |
| `create_fusion_node_chain` | `node_chain[]` | success/failure sentence |
| `open_page` | `page_name` (`media`/`edit`/`fusion`/`color`/`fairlight`/`deliver`) | success/failure sentence |
| `execute_python` | `code` | `str(result)` when the code assigns `result`, else `"Code executed successfully."` |
| `execute_lua` | `script` | string result |

Resources: `system://status`, `project://timelines`, `project://current`, `mediapool://folders`, `timeline://current`.

## percept (28 tools: 25 core + 3 extras)

### Perception — text first

| Tool | Arguments | Returns | Cost |
|---|---|---|---|
| `probe` | `path` | duration, fps, size, codec, audio layout, loudness | ~80 tok |
| `index_media` | `paths[]`, `step_s` | per file: duration, scene cuts, luminance/colour/motion profile, audio summary, speech runs | ~150 tok per minute, 0 when cached |
| `audio_index` | `path`, `window_s` | loudness windows, silences, speech-likeness runs | text |
| `transcribe` | `path`, `size`, `language`, `allow_download` | timed segments (local whisper.cpp) | text |
| `contact_sheet` | `path`, `start_s`, `end_s`, `cols`, `rows`, `width` | **one PNG grid** + row-major time map | 1 image |
| `timeline_report` | — | shots, source frames, audio tracks of the open timeline | text |
| `timeline_storyboard` | `cols`, `rows`, `width`, `track` | **one PNG grid** of the timeline + shot map | 1 image |

### Automation — one call

| Tool | Arguments | Returns |
|---|---|---|
| `sfx_tags` | — | 29 semantic sound tags with default gain offsets |
| `list_recipes` | — | recipe menu: sections, beats, purpose, SFX |
| `apply_plan` | `plan`, `dry_run` | builds the timeline; returns length, shot and cue counts |
| `apply_recipe` | `name`, `shots[]`, `timeline`, `camera_bed`, `music`, `dry_run` | same, with rhythm and SFX from the recipe |

### Resolve-native actions — what only the app can do

| Tool | Arguments | Returns |
|---|---|---|
| `timeline_qc` | — | structural verdict: gaps, black ranges, tail, audio clip count, markers | 
| `qc_media` | `path` | duration, resolution, integrated LUFS, true peak, black spans, silence starts, problem list |
| `captions` | `language`, `chars_per_line`, `preset`, `srt_path` | creates subtitles from timeline audio, writes SRT, reports too-long / too-fast lines |
| `normalize_loudness` | `target_lufs`, `mode` | normalizes every audio clip; lists available modes |
| `remove_silence` | `threshold_db`, `minimum_frames`, `crossfade` | ripple-deletes pauses on the first audio track |
| `smart_reframe` | *(extra)* `track`, `track_index` | runs Resolve SmartReframe and polls its status |
| `add_markers` | `markers[]` | places review markers (frame, name, note, color, duration) |
| `list_markers` | — | reads markers back as text — the cheap side of a human review loop |
| `clear_markers` | `color` | deletes markers by color or all |

### Transcript, b-roll, music and multicam

| Tool | Arguments | Returns |
|---|---|---|
| `transcribe_clip` | `name`, `size`, `language`, `use_resolve` | timed segments; Resolve's engine first, whisper fallback; cached |
| `transcript_index` | `bin_name`, `limit` | transcribes up to N clips of a bin and caches them |
| `string_out` | `query`, `timeline`, `camera_bed`, `dry_run` | rough cut: one shot per transcript segment containing the phrase |
| `b_roll` | *(extra)* `keywords`, `limit`, `min_motion` | ranked clips: transcript hits first, then luminance/motion fit |
| `assemble` | `brief`, `timeline`, `tags`, `camera_bed`, `music`, `recipe`, `dry_run` | meaning-first cut: roles resolved against a shot-tags file, rhythm and SFX from a recipe |
| `music_index` | `folder` | BPM, loudness and length per track, cached |
| `match_music` | `bpm`, `duration_s`, `folder` | tracks ranked by tempo closeness (octave-folded) and length |
| `create_multicam` | *(extra)* `clip_names`, `name`, `sync` | multicam clip synced by audio waveform |

`assemble` closes the gap left by `string_out`: transcript search needs speech, and most b-roll has
none. A shot-tags file (`examples/shot-tags.example.json`) gives every shot roles — `fire`, `chop`,
`sauce`, `garnish` — and a brief of those roles becomes a timeline. Tag the footage once from a
contact sheet; afterwards the cut costs one call.

## Sound tags

`knife`, `slice`, `peel`, `sizzle`, `fry`, `ignite`, `boil`, `pour`, `whisk`, `stir`, `squish`,
`sprinkle`, `squeeze`, `crack`, `plate`, `cutlery`, `glass`, `door`, `bag`, `beep`, `whoosh`,
`impact`, `impact_soft`, `bell`, `click`, `water`, `ambience`, `timer`.

Each tag carries a default gain offset (dB above the ambience bed). Repeated tags rotate through the
tag's files, so six knife hits are not the same sample. Resolution reads
`PERCEPT_SFX_CATALOG` (CSV with `file`, `category`, `duration_s`, `description`) and
`PERCEPT_SFX_LIBRARY` (root of the WAV tree).

## Cache layout

| Path | Contents |
|---|---|
| `~/.cache/percept/files/<hash>.json` | per-file storyboard index, keyed by path+size+mtime+step |
| `~/.cache/percept/sfx/<frame>_<tag>_<hash>.wav` | baked cue audio with computed gain |
| `~/.cache/percept/models/ggml-*.bin` | whisper.cpp models |
| `~/.cache/percept/transcripts/` | per-clip transcript segments |
| `~/.cache/percept/music/` | per-track BPM/loudness index |
