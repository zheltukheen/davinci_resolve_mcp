# Tool reference

Two servers, 25 tools. `resolve` controls the app; `percept` decides and assembles.

## resolve (14 tools)

Text in, text out. Errors come back as prose (`"Error: Not connected…"`), successes as sentences.

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

## percept (11 tools)

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
