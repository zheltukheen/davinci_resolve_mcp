# DaVinci Resolve (MCP)

A configuration-only bundle that mounts one `@deepseek-ai/dsh-mcp-client` row named `resolve`
and starts a local stdio MCP server that talks to a running DaVinci Resolve through Blackmagic's
scripting API.

## What it gives the agent

Tools appear as `mcp__resolve__*`:

| Group | Tools |
|---|---|
| Projects | `create_project`, `load_project`, `save_project` |
| Timelines | `create_timeline`, `set_current_timeline`, `create_timeline_from_clips` |
| Media pool | `import_media`, `create_folder` |
| Fusion | `add_fusion_comp_to_clip`, `create_fusion_node`, `create_fusion_node_chain` |
| UI | `open_page` |
| Escape hatch | `execute_python`, `execute_lua` |

Reading state uses the escape hatch: `execute_python` returns `str(result)` when the code assigns
to a variable named `result`, for example `result = current_project.GetName()`.

## Requirements

- DaVinci Resolve (or Resolve Studio) running and logged in on this machine; the free version
  exposes a smaller part of the scripting API.
- Python 3.12 interpreter in the vendored venv at `server/.venv` (created with
  `uv sync --python 3.12 --no-dev` in `server/`).
- The server talks to the Resolve instance on `127.0.0.1`, so Resolve must be open before the
  agent needs the tools. Closing Resolve leaves the tools in place but every call fails until it
  is reopened.

## Layout

- `package.json` — bundle manifest, `dsh.bundle.patch` points at `cordis.patch.yml`.
- `cordis.patch.yml` — inserts the `mcp-client` row with the stdio command, cwd, and timeouts.
- `server/` — vendored copy of `apvlv/davinci-resolve-mcp` (MIT, © Sage Choi) plus its uv venv.
- `locale/en.json`, `locale/ru.json` — display title and description.

## Turning it off

Disable the `DaVinci Resolve` bundle in **Settings → Plugins** (or `plugin_manager` `set_bundle`);
the MCP row unmounts and the `mcp__resolve__*` tools disappear. Resolve itself is untouched.
