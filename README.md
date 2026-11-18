# DaVinci Resolve MCP

Two local MCP servers that let an AI agent run a DaVinci Resolve edit without burning context
on video frames:

| Package | Tools | What it does |
|---|---|---|
| [`packages/resolve-mcp`](packages/resolve-mcp) | 14 | Resolve control: projects, timelines, media pool, Fusion, pages, raw Python/Lua execution |
| [`packages/percept-mcp`](packages/percept-mcp) | 11 | Perception (text indexes, contact sheets) and deterministic assembly (`apply_plan`, `apply_recipe`) |

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

### Install as DeepSeek Harness bundles

Each package also ships a `cordis.patch.yml` and `package.json`, so it installs as a DSH bundle
(Bundles → install a local directory). The patch mounts one `@deepseek-ai/dsh-mcp-client` row per
server; edit `command`/`cwd` to your checkout.

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
