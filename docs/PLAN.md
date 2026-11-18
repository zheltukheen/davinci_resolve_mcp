# Plan and recipe schema

## Plan

```json
{
  "timeline": "Reel take 3",
  "fps": 50,
  "start_frame": 0,
  "beat_grid": {"bpm": 110},
  "bed_db": -29,
  "camera_bed": "/abs/path/camera.wav",
  "music": "/abs/path/track.wav",
  "shots": [
    {"clip": "ZHEL0500.mp4", "src_in": 100, "beats": 2},
    {"clip": "ZHEL0501.mp4", "src_in": 34, "frames": 82}
  ],
  "sfx": [
    {"frame": 0, "tag": "sizzle", "dur_s": 1.2},
    {"frame": 82, "tag": "knife", "dur_s": 0.35, "offset_db": 14, "variant": 2}
  ]
}
```

| Field | Required | Meaning |
|---|---|---|
| `timeline` | no | name of the timeline to create; an existing one with the same name is replaced |
| `fps` | no | frame rate for beat conversion, default 50 |
| `start_frame` | no | first frame of the first shot, default 0 |
| `beat_grid.bpm` | no | lets shots use `beats` instead of `frames`; frames per beat = `fps * 60 / bpm` |
| `bed_db` | no | loudness of the ambience bed used for SFX gain, default −29 dBFS |
| `camera_bed` | no | audio file appended to A1 from frame 0 |
| `music` | no | audio file appended to A2 from frame 0 |
| `shots[].clip` | yes | media pool clip name |
| `shots[].src_in` | no | source frame where the shot starts, default 0 |
| `shots[].frames` / `beats` | one of | duration; with `beat_grid`, `beats` wins if both are absent |
| `sfx[].frame` | yes | absolute frame on the new timeline |
| `sfx[].tag` | yes | semantic tag resolved by `sfx_tags` |
| `sfx[].dur_s` | no | trimmed length of the cue; longer library files are cut from their loudest window |
| `sfx[].offset_db` | no | overrides the tag's default gain offset |
| `sfx[].variant` | no | picks a specific file of the tag instead of rotating |

### What the executor does

1. Validates the plan and converts beats to frames; with a beat grid it reports shots that sit more
   than half a frame off the beats.
2. Resolves every `sfx.tag` to a library file, rotating variants per repeated tag.
3. Bakes each cue: cut from the loudest window, gain = `bed_db + offset_db` relative to the cue's
   RMS, peak capped at −4.4 dBFS, written to `~/.cache/percept/sfx/` (reused across timelines).
4. Deletes a timeline with the same name, creates a new one, appends shots to V1, bed to A1, music
   to A2, cues greedily to A3 and above so overlaps never sit on one track.
5. Saves the project and returns a summary.

`dry_run: true` stops after step 2 and returns the resolved shot list and cue names — no Resolve
needed, no images, cheap to iterate.

### Reported warnings

- `shot at N is +x frames off the beat grid` — rhythm drift from mixing frames and beats.
- `clips not in the media pool` — the plan names a clip Resolve does not know.

## Recipe

```json
{
  "name": "reel_frying",
  "title": "Reel: frying / grill",
  "fps": 50,
  "bpm": 110,
  "sections": [
    {"id": "open", "beats": 2, "purpose": "wide opening shot of the pan",
     "sfx": [{"beat": 0, "tag": "sizzle", "dur_s": 1.2}]},
    {"id": "cut", "beats": 3, "purpose": "knife work",
     "sfx": [{"beat": 0, "tag": "knife", "dur_s": 0.35},
             {"beat": 0.5, "tag": "knife", "dur_s": 0.35}]}
  ]
}
```

A recipe fixes the rhythm and the sound design; the caller supplies only clips:

```json
{"name": "reel_frying",
 "shots": [{"section": "open", "clip": "ZHEL0500.mp4", "src_in": 100},
           {"section": "cut", "clip": "ZHEL0501.mp4", "src_in": 34}]}
```

`beat` inside `sfx` is an offset from the section start, in beats, so half-beat knife hits stay
musical at any tempo. Section `id`s may repeat in the assignment list (a second `cut` section is a
second knife sequence).

Shipped recipes: `reel_frying`, `reel_assembly`. Add a JSON file to `packages/percept-mcp/recipes/`
(or point `PERCEPT_RECIPES` elsewhere) — no code change needed.
