#!/usr/bin/env python3
"""JSON-first Resolve MCP server.

Same 14 operations as the string-returning server, but every result is a JSON
object: {"ok": true, ...} on success, {"ok": false, "error": "<code>",
"message": "..."} on failure. Stable codes and stable field names are what let
an automated chain branch on outcomes instead of parsing prose.
"""
import json
import os
import sys

from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
from davinci_resolve_mcp.resolve_api import ResolveAPI  # noqa: E402

API = ResolveAPI()

server = Server("resolve")

ERRORS = {
    "no_resolve": "DaVinci Resolve is not running or the scripting API is unreachable",
    "no_project": "no project is open",
    "no_timeline": "no timeline is open",
    "no_media_pool": "the media pool is unavailable",
    "not_found": "the requested object does not exist",
    "invalid_argument": "the argument is out of range or malformed",
    "failed": "Resolve refused the operation",
}


def ok(action, **fields):
    return TextContent(type="text", text=json.dumps({"ok": True, "action": action, **fields},
                                                    ensure_ascii=False))


def fail(code, message=None, **fields):
    return TextContent(type="text",
                       text=json.dumps({"ok": False, "error": code,
                                        "message": message or ERRORS.get(code, code), **fields},
                                       ensure_ascii=False))


def guard():
    """Return (api, project, error content)."""
    if not API.is_connected():
        return None, None, fail("no_resolve")
    project = API.get_current_project()
    if project is None:
        return None, None, fail("no_project")
    return API, project, None


def jsonable(value):
    """Best-effort conversion of a Resolve object or value into JSON-able data."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    for attribute in ("GetName",):
        method = getattr(value, attribute, None)
        if callable(method):
            try:
                return method()
            except Exception:  # noqa: BLE001
                pass
    return repr(value)


# ---------------------------------------------------------------- tools
def create_project(name):
    api, project, error = guard()
    if error:
        return error
    manager = api.get_project_manager()
    created = manager.CreateProject(name) if manager else None
    if created:
        return ok("create_project", project=name)
    return fail("failed", f"project '{name}' was not created (it may already exist)", project=name)


def load_project(name):
    api, project, error = guard()
    if error:
        return error
    manager = api.get_project_manager()
    loaded = manager.LoadProject(name) if manager else None
    if loaded:
        return ok("load_project", project=name)
    return fail("not_found", f"project '{name}' not found", project=name)


def save_project():
    api, project, error = guard()
    if error:
        return error
    manager = api.get_project_manager()
    return ok("save_project", saved=bool(manager.SaveProject()) if manager else False)


def create_timeline(name):
    api, project, error = guard()
    if error:
        return error
    created = api.get_media_pool().CreateEmptyTimeline(name) if api.get_media_pool() else None
    if created:
        timeline = api.get_current_timeline()
        return ok("create_timeline", timeline=name,
                  frames=timeline.GetEndFrame() if timeline else 0)
    return fail("failed", f"timeline '{name}' was not created", timeline=name)


def set_current_timeline(index):
    api, project, error = guard()
    if error:
        return error
    try:
        index = int(index)
    except (TypeError, ValueError):
        return fail("invalid_argument", "index must be an integer")
    if index < 1 or index > project.GetTimelineCount():
        return fail("invalid_argument", f"index must be between 1 and {project.GetTimelineCount()}",
                    index=index, count=project.GetTimelineCount())
    timeline = api.get_timeline_by_index(index)
    if not timeline:
        return fail("not_found", f"no timeline at index {index}", index=index)
    switched = project.SetCurrentTimeline(timeline)
    return ok("set_current_timeline", timeline=timeline.GetName(), index=index, switched=bool(switched))


def create_timeline_from_clips(name, clip_indices):
    api, project, error = guard()
    if error:
        return error
    if not clip_indices:
        return fail("invalid_argument", "clip_indices is empty")
    created = api.create_timeline_from_clips(name, list(clip_indices))
    if created:
        return ok("create_timeline_from_clips", timeline=name, clips=len(clip_indices))
    return fail("failed", f"timeline '{name}' was not created from {len(clip_indices)} clips")


def import_media(file_paths):
    api, project, error = guard()
    if error:
        return error
    if not file_paths:
        return fail("invalid_argument", "file_paths is empty")
    missing = [path for path in file_paths if not os.path.exists(path)]
    existing = [path for path in file_paths if os.path.exists(path)]
    imported = api.add_items_to_media_pool(existing) if existing else []
    names = [jsonable(item) for item in (imported or [])]
    return ok("import_media", imported=len(names), names=names,
              missing=missing, requested=len(file_paths))


def create_folder(name):
    api, project, error = guard()
    if error:
        return error
    folder = api.add_sub_folder(name)
    if folder:
        return ok("create_folder", folder=name)
    return fail("failed", f"folder '{name}' was not created")


def add_fusion_comp_to_clip(timeline_index, track_type, track_index, item_index):
    api, project, error = guard()
    if error:
        return error
    timeline = project.GetTimelineByIndex(int(timeline_index))
    if not timeline:
        return fail("not_found", f"no timeline at index {timeline_index}")
    items = timeline.GetItemsInTrack(track_type, int(track_index)) or {}
    item = items.get(int(item_index))
    if not item:
        return fail("not_found", f"no item {item_index} on {track_type} {track_index}")
    comp = item.AddFusionComp()
    if comp:
        return ok("add_fusion_comp_to_clip", comp_count=item.GetFusionCompCount(),
                  timeline_index=int(timeline_index), item_index=int(item_index))
    return fail("failed", "Resolve refused to add a Fusion composition")


def create_fusion_node(node_type, parameters=None):
    api, project, error = guard()
    if error:
        return error
    node = api.create_fusion_node(node_type, parameters or {})
    if node:
        return ok("create_fusion_node", node_type=node_type)
    return fail("failed", f"Fusion node '{node_type}' was not created")


def create_fusion_node_chain(node_chain):
    api, project, error = guard()
    if error:
        return error
    if not node_chain:
        return fail("invalid_argument", "node_chain is empty")
    created = 0
    for node in node_chain:
        node_type = node.get("type") if isinstance(node, dict) else None
        if not node_type:
            continue
        if api.create_fusion_node(node_type, (node.get("params") if isinstance(node, dict) else None) or {}):
            created += 1
    if created:
        return ok("create_fusion_node_chain", nodes=created)
    return fail("failed", "the Fusion node chain was not created")


def open_page(page_name):
    api, project, error = guard()
    if error:
        return error
    valid = ["media", "edit", "fusion", "color", "fairlight", "deliver"]
    page = str(page_name).lower()
    if page not in valid:
        return fail("invalid_argument", f"page must be one of {valid}", page=page_name)
    if api.open_page(page):
        return ok("open_page", page=page)
    return fail("failed", f"page '{page}' did not open")


def execute_python(code):
    api, project, error = guard()
    if error:
        return error
    resolve = api.resolve
    namespace = {"resolve": resolve, "project": project,
                 "project_manager": api.get_project_manager(),
                 "media_pool": api.get_media_pool(),
                 "media_storage": api.get_media_storage(),
                 "fusion": api.get_fusion()}
    try:
        exec(code, {"__builtins__": __builtins__}, namespace)  # noqa: S102
    except Exception as exception:  # noqa: BLE001
        return fail("failed", f"{type(exception).__name__}: {exception}")
    if "result" in namespace:
        return ok("execute_python", result=jsonable(namespace["result"]))
    return ok("execute_python", result=None)


def execute_lua(script):
    api, project, error = guard()
    if error:
        return error
    try:
        value = api.execute_lua(script)
    except Exception as exception:  # noqa: BLE001
        return fail("failed", f"{type(exception).__name__}: {exception}")
    return ok("execute_lua", result=jsonable(value))


TOOLS = [
    Tool(name="create_project", description="Create a project. JSON result.",
         inputSchema={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}),
    Tool(name="load_project", description="Load a project. JSON result.",
         inputSchema={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}),
    Tool(name="save_project", description="Save the open project. JSON result.",
         inputSchema={"type": "object", "properties": {}}),
    Tool(name="create_timeline", description="Create an empty timeline. JSON result.",
         inputSchema={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}),
    Tool(name="set_current_timeline", description="Switch the current timeline by 1-based index.",
         inputSchema={"type": "object", "properties": {"index": {"type": "integer"}}, "required": ["index"]}),
    Tool(name="create_timeline_from_clips", description="Build a timeline from media pool clips.",
         inputSchema={"type": "object",
                      "properties": {"name": {"type": "string"},
                                     "clip_indices": {"type": "array", "items": {"type": "integer"}}},
                      "required": ["name", "clip_indices"]}),
    Tool(name="import_media", description="Import files into the media pool; reports missing paths.",
         inputSchema={"type": "object", "properties": {"file_paths": {"type": "array",
                                                                     "items": {"type": "string"}}},
                      "required": ["file_paths"]}),
    Tool(name="create_folder", description="Create a media pool folder in the current bin.",
         inputSchema={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}),
    Tool(name="add_fusion_comp_to_clip", description="Add a Fusion composition to a timeline item.",
         inputSchema={"type": "object",
                      "properties": {"timeline_index": {"type": "integer"},
                                     "track_type": {"type": "string"},
                                     "track_index": {"type": "integer"},
                                     "item_index": {"type": "integer"}},
                      "required": ["timeline_index", "track_type", "track_index", "item_index"]}),
    Tool(name="create_fusion_node", description="Create a node in the current Fusion composition.",
         inputSchema={"type": "object",
                      "properties": {"node_type": {"type": "string"},
                                     "parameters": {"type": "object"}},
                      "required": ["node_type"]}),
    Tool(name="create_fusion_node_chain", description="Create a connected chain of Fusion nodes.",
         inputSchema={"type": "object", "properties": {"node_chain": {"type": "array",
                                                                     "items": {"type": "object"}}},
                      "required": ["node_chain"]}),
    Tool(name="open_page", description="Switch Resolve's page.",
         inputSchema={"type": "object", "properties": {"page_name": {"type": "string"}},
                      "required": ["page_name"]}),
    Tool(name="execute_python", description="Run Python in Resolve; returns {result} when the code sets result.",
         inputSchema={"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}),
    Tool(name="execute_lua", description="Run Lua in Fusion.",
         inputSchema={"type": "object", "properties": {"script": {"type": "string"}}, "required": ["script"]}),
]

HANDLERS = {
    "create_project": create_project, "load_project": load_project, "save_project": save_project,
    "create_timeline": create_timeline, "set_current_timeline": set_current_timeline,
    "create_timeline_from_clips": create_timeline_from_clips, "import_media": import_media,
    "create_folder": create_folder, "add_fusion_comp_to_clip": add_fusion_comp_to_clip,
    "create_fusion_node": create_fusion_node,
    "create_fusion_node_chain": create_fusion_node_chain, "open_page": open_page,
    "execute_python": execute_python, "execute_lua": execute_lua,
}


@server.list_tools()
async def list_tools():
    return TOOLS


@server.call_tool()
async def call_tool(name, arguments):
    handler = HANDLERS.get(name)
    if handler is None:
        return [fail("invalid_argument", f"unknown tool {name}")]
    try:
        return [handler(**arguments)]
    except TypeError as error:
        return [fail("invalid_argument", str(error))]
    except Exception as error:  # noqa: BLE001
        return [fail("failed", f"{type(error).__name__}: {error}")]


async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
