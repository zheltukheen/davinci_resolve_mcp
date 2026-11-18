# DaVinci Resolve MCP

Two local MCP servers that let an AI agent run a DaVinci Resolve edit without burning context
on video frames:

| Package | Tools | What it does |
|---|---|---|
| [`packages/resolve-mcp`](packages/resolve-mcp) | 14 | Resolve control in JSON: projects, timelines, media pool, Fusion, pages, raw Python/Lua execution |
| [`packages/percept-mcp`](packages/percept-mcp) | 27 | Perception (text indexes, contact sheets), deterministic assembly (`apply_plan`, `apply_recipe`), Resolve-native actions (captions, loudness, markers, QC), transcript string-out, b-roll ranking, music tempo matching, multicam sync |

Everything is local: stdio MCP servers, Resolve's own scripting API, ffmpeg/ffprobe, and a local
sound library. No cloud services, no API keys.

## Why two servers

Resolve's API is a control surface: it can build a timeline but it cannot *see* one. Feeding frames
to a model is the expensive part of automated editing — one image per shot, per iteration. The
second server exists to invert that:

- **numbers first** — `index_media` returns duration, scene cuts, loudness, speech runs and a
  luminance/colour/motion profile as text, cached by file hash;
- **one picture per request** — `contact_sheet` packs 16–64 moments into a single grid;
- **one call per edit** — `apply_plan` / `apply_recipe` build a whole timeline from a text plan,
  resolving semantic sound tags (`knife`, `sizzle`, `impact`) locally.

A 20-shot reel becomes: one text index pass, one plan, one call, one storyboard image to verify.

Everything is configurable without editing code: `PERCEPT_SFX_LIBRARY` and `PERCEPT_SFX_CATALOG`
point at a sound library, `PERCEPT_CACHE` moves the cache, `PERCEPT_RECIPES` moves the recipe
folder. B-roll ranking and multicam sync are opt-in extras (`PERCEPT_EXTRA_TOOLS=1`) because most
edits do not need them.

## Quick start

Requirements: macOS, DaVinci Resolve (Studio recommended; some features are Studio-only), Python
3.12, [uv](https://docs.astral.sh/uv/), ffmpeg/ffprobe on `PATH`.

```bash
git clone https://github.com/zheltukheen/davinci_resolve_mcp
cd davinci_resolve_mcp

# resolve bridge
cd packages/resolve-mcp/server
uv venv --python 3.12 && uv pip install -e .
cd ../../../percept-mcp/server
uv venv --python 3.12 && uv pip install "mcp<2" numpy
```

Both servers speak MCP over stdio. Any MCP client can start them:

```json
{
  "mcpServers": {
    "resolve": {
      "command": "/abs/path/packages/resolve-mcp/server/.venv/bin/davinci-resolve-mcp"
    },
    "percept": {
      "command": "/abs/path/packages/percept-mcp/server/.venv/bin/python",
      "args": ["/abs/path/packages/percept-mcp/server/server.py"]
    }
  }
}
```

### Two ways to assemble

- **From speech** — `transcribe_clip` / `transcript_index` / `string_out`: one shot per transcript
  segment matching a phrase.
- **From meaning** — `assemble`: a brief of roles (`fire, chop, sauce, hero`) resolved against a
  shot-tags file, with rhythm and sound design coming from a recipe. This works on footage with no
  speech at all, which is most b-roll.

`b_roll`, `create_multicam` and `smart_reframe` are opt-in extras (`PERCEPT_EXTRA_TOOLS=1`) rather
than core tools: they are occasional operations, and every extra tool costs schema context in every
request.

### Install as DeepSeek Harness bundles

Each package ships `package.json` and a `cordis.patch.template.yml`. Render the machine-specific
patch first, then install the directory as a DSH bundle (Bundles → install a local directory):

```bash
python3 scripts/render-patches.py packages/resolve-mcp packages/percept-mcp
```

The rendered `cordis.patch.yml` is gitignored and mounts one `@deepseek-ai/dsh-mcp-client` row per
server with this checkout's absolute paths.

## The editing plan

A plan is text. Nothing interactive, nothing guessed:

```json
{
  "timeline": "Reel take 3",
  "fps": 50,
  "beat_grid": {"bpm": 110},
  "camera_bed": "/path/camera.wav",
  "shots": [
    {"clip": "ZHEL0500.mp4", "src_in": 100, "beats": 2},
    {"clip": "ZHEL0501.mp4", "src_in": 34, "beats": 3}
  ],
  "sfx": [
    {"frame": 0, "tag": "sizzle", "dur_s": 1.2},
    {"frame": 55, "tag": "knife", "dur_s": 0.35}
  ]
}
```

`apply_plan` validates it, converts beats to frames, resolves every `tag` to a real library file,
bakes cue audio with bed-relative gain, and builds the timeline in one call. `dry_run` returns the
resolved shot list without touching Resolve. Recipes (`apply_recipe`) go further: the section
rhythm and the sound pattern live in JSON, so the caller supplies only a clip and an in-point per
section. Full schema: [docs/PLAN.md](docs/PLAN.md). Tool reference: [docs/TOOLS.md](docs/TOOLS.md).

## Requirements and limits

- Resolve must be running for any `timeline_*` or `apply_*` call; clips must already be in the media
  pool.
- The SFX layer needs a sound library on disk (`PERCEPT_SFX_LIBRARY`, `PERCEPT_SFX_CATALOG`); the
  tags map onto whatever WAV files are there.
- `SmartReframe`, subtitles and audio classification are Studio features.
- Scene detection is a heuristic; a single continuous take reports `cuts=0`.
- Nothing here listens to the result: levels are measured, not auditioned.

## License

Our code is MIT (see [LICENSE](LICENSE)). The Resolve bridge vendors
[apvlv/davinci-resolve-mcp](https://github.com/apvlv/davinci-resolve-mcp) (MIT, © Sage Choi) — see
`packages/resolve-mcp/server/LICENSE`. No third-party audio is bundled.
