#!/usr/bin/env python3
"""Perception + automation MCP server for Resolve work.

Design: the agent should spend almost no image tokens and few tool calls.
Numbers first, one image per request when a picture is really needed, and a whole
edit assembled by a single deterministic call from a text plan or a recipe.
"""
import asyncio
import base64
import hashlib
import json
import os
import re
import subprocess
import sys

import numpy as np
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.types import ImageContent, TextContent, Tool

import audio_index
import planner
import recipes
import sfx_library

RESOLVE_MODULES = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules"
CACHE = os.path.expanduser("~/.cache/percept")
FILE_CACHE = os.path.join(CACHE, "files")
MAX_SHEET_FRAMES = 64

server = Server("percept")


# ---------------------------------------------------------------- helpers
def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, check=True, **kw)


def probe_file(path):
    out = run(["ffprobe", "-v", "error", "-print_format", "json",
               "-show_format", "-show_streams", path]).stdout
    info = json.loads(out)
    video = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    audio = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    return {
        "file": os.path.basename(path),
        "duration_s": round(float(info["format"].get("duration", 0)), 2),
        "fps": video["r_frame_rate"] if video else None,
        "size": f'{video["width"]}x{video["height"]}' if video else None,
        "codec": video["codec_name"] if video else None,
        "audio": f'{audio["channels"]}ch {audio["sample_rate"]}Hz' if audio else None,
    }


def loudness(path):
    out = subprocess.run(["ffmpeg", "-hide_banner", "-i", path, "-af", "volumedetect",
                          "-f", "null", "-"], capture_output=True, text=True).stderr
    mean = re.search(r"mean_volume: (-?[\d.]+)", out)
    peak = re.search(r"max_volume: (-?[\d.]+)", out)
    return (float(mean.group(1)) if mean else None,
            float(peak.group(1)) if peak else None)


def scene_cuts(path, threshold=0.3, limit=200):
    out = subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-i", path,
                          "-vf", f"select='gt(scene,{threshold})',showinfo",
                          "-f", "null", "-"], capture_output=True, text=True).stderr
    return [float(t) for t in re.findall(r"pts_time:([\d.]+)", out)][:limit]


def frames_rgb(path, start_s, end_s, count, width):
    span = max(end_s - start_s, 1e-3)
    fps = count / span
    raw = run(["ffmpeg", "-v", "error", "-ss", f"{start_s:.3f}", "-t", f"{span:.3f}",
               "-i", path, "-vf", f"fps={fps:.6f},scale={width}:-2", "-frames:v", str(count),
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]).stdout
    probe = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                 "stream=width,height", "-of", "csv=p=0", path]).stdout.decode().strip()
    dims = [int(x) for x in re.findall(r"\d+", probe)][:2]
    src_w, src_h = (dims + [width, width * 16 // 9])[:2]
    tile_h = int(round(width * src_h / src_w / 2)) * 2
    tile_size = width * tile_h * 3
    if len(raw) < tile_size:
        raise RuntimeError("no frames decoded")
    frames = [raw[i:i + tile_size] for i in range(0, len(raw) - tile_size + 1, tile_size)][:count]
    return frames, tile_h


def mosaic(frames, tile_w, tile_h, cols, rows):
    canvas = np.zeros((rows * tile_h, cols * tile_w, 3), dtype=np.uint8)
    for index, frame in enumerate(frames[: cols * rows]):
        tile = np.frombuffer(frame, dtype=np.uint8).reshape(tile_h, tile_w, 3)
        row, col = divmod(index, cols)
        canvas[row * tile_h:(row + 1) * tile_h, col * tile_w:(col + 1) * tile_w] = tile
    return canvas


def png_bytes(canvas):
    height, width = canvas.shape[:2]
    return run(["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                "-s", f"{width}x{height}", "-i", "-", "-frames:v", "1", "-f", "image2pipe",
                "-vcodec", "png", "-"], input=canvas.tobytes()).stdout


def image_tool(canvas, caption):
    return [ImageContent(type="image", data=base64.b64encode(png_bytes(canvas)).decode(),
                         mimeType="image/png"),
            TextContent(type="text", text=caption)]


def frame_feature(frame, tile_w, tile_h):
    pixels = np.frombuffer(frame, dtype=np.uint8).reshape(tile_h, tile_w, 3).astype(np.float32)
    return {"lum": float(pixels.mean()), "color": float((pixels.max(axis=2) - pixels.min(axis=2)).mean())}


def file_key(path, step_s):
    stat = os.stat(path)
    raw = f"{path}:{stat.st_size}:{int(stat.st_mtime)}:{step_s}"
    return hashlib.sha1(raw.encode()).hexdigest()[:20]


def index_one(path, step_s):
    """Cached per-file storyboard index; a repeat call costs no ffmpeg work."""
    os.makedirs(FILE_CACHE, exist_ok=True)
    key = file_key(path, step_s)
    cached = os.path.join(FILE_CACHE, f"{key}.json")
    if os.path.exists(cached):
        data = json.load(open(cached, encoding="utf-8"))
        data["cached"] = True
        return data

    facts = probe_file(path)
    duration = facts["duration_s"]
    count = max(2, min(MAX_SHEET_FRAMES, int(duration / step_s) + 1))
    frames, tile_h = frames_rgb(path, 0.0, max(duration - 0.05, 0.05), count, 160)
    features = [frame_feature(frame, 160, tile_h) for frame in frames]
    motion, previous = [], None
    for frame in frames:
        current = np.frombuffer(frame, dtype=np.uint8).reshape(tile_h, 160, 3).astype(np.int16)
        motion.append(0.0 if previous is None else float(np.abs(current - previous).mean()))
        previous = current
    audio = audio_index.summarize(path) if facts["audio"] else None
    data = {
        **facts,
        "step_s": round(duration / max(len(frames) - 1, 1), 2),
        "samples": [{"t": round(i * duration / max(len(frames) - 1, 1), 2),
                     "lum": round(features[i]["lum"], 1),
                     "color": round(features[i]["color"], 1),
                     "motion": round(motion[i], 2)} for i in range(len(frames))],
        "scene_cuts_s": [round(t, 2) for t in scene_cuts(path)],
        "max_motion": round(max(motion), 2),
        "lum_range": [round(min(f["lum"] for f in features), 1),
                      round(max(f["lum"] for f in features), 1)],
        "audio": audio,
        "cached": False,
    }
    json.dump(data, open(cached, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return data


def format_index(data):
    audio = data.get("audio") or {}
    speech = audio.get("speech_runs") or []
    line = (f'- {data["file"]}: {data["duration_s"]}s {data.get("size")} @{data.get("fps")} '
            f'motion_max={data["max_motion"]} lum={data["lum_range"]} '
            f'cuts={len(data["scene_cuts_s"])}')
    if audio:
        line += (f' | audio mean {audio["rms_db"][0]}dB, voice {audio["voice_ratio"][1]}, '
                 f'speech {len(speech)} run(s)')
        if speech:
            line += " " + str(speech[:3])
    if data.get("cached"):
        line += " (cached)"
    return line


# ---------------------------------------------------------------- resolve helpers
def load_resolve():
    if RESOLVE_MODULES not in sys.path:
        sys.path.append(RESOLVE_MODULES)
    try:
        import DaVinciResolveScript as dvr
    except ImportError:
        return None
    return dvr.scriptapp("Resolve")


NO_RESOLVE = "DaVinci Resolve is not running: start it and open the project, then call this again."


# ---------------------------------------------------------------- tools
TOOLS = [
    Tool(name="probe",
         description="Media facts and loudness. Text only.",
         inputSchema={"type": "object", "properties": {"path": {"type": "string"}},
                      "required": ["path"]}),
    Tool(name="index_media",
         description=("Cached storyboard index: duration, scene cuts, luminance/colour/motion profile, "
                      "loudness, speech runs. Text only, no images. Repeat calls on unchanged files are free."),
         inputSchema={"type": "object",
                      "properties": {"paths": {"type": "array", "items": {"type": "string"}},
                                     "step_s": {"type": "number", "default": 1.0}},
                      "required": ["paths"]}),
    Tool(name="audio_index",
         description=("Audio-first pass over one file: loudness windows, silences, speech-likeness runs. "
                      "Cheaper and usually more useful than looking at frames. Text only."),
         inputSchema={"type": "object",
                      "properties": {"path": {"type": "string"},
                                     "window_s": {"type": "number", "default": 0.5}},
                      "required": ["path"]}),
    Tool(name="transcribe",
         description=("Speech to text with local whisper.cpp. Returns timed segments. "
                      "Set allow_download when the model file is missing."),
         inputSchema={"type": "object",
                      "properties": {"path": {"type": "string"},
                                     "size": {"type": "string", "default": "tiny"},
                                     "language": {"type": "string"},
                                     "allow_download": {"type": "boolean", "default": False}},
                      "required": ["path"]}),
    Tool(name="contact_sheet",
         description="One image grid of evenly spaced frames for a time range, plus the time map.",
         inputSchema={"type": "object",
                      "properties": {"path": {"type": "string"}, "start_s": {"type": "number"},
                                     "end_s": {"type": "number"}, "cols": {"type": "integer", "default": 4},
                                     "rows": {"type": "integer", "default": 4},
                                     "width": {"type": "integer", "default": 180}},
                      "required": ["path", "start_s", "end_s"]}),
    Tool(name="timeline_report",
         description="Current Resolve timeline as text: shots, frame ranges, source frames, audio.",
         inputSchema={"type": "object", "properties": {}}),
    Tool(name="timeline_storyboard",
         description="One image grid of the current timeline, one frame per shot, plus the shot map.",
         inputSchema={"type": "object",
                      "properties": {"cols": {"type": "integer", "default": 5},
                                     "rows": {"type": "integer", "default": 4},
                                     "width": {"type": "integer", "default": 160},
                                     "track": {"type": "integer", "default": 1}}}),
    Tool(name="sfx_tags",
         description="Semantic SFX tags of the local library with their default gain offsets.",
         inputSchema={"type": "object", "properties": {}}),
    Tool(name="list_recipes",
         description="Available edit recipes: beat-locked section templates with their SFX.",
         inputSchema={"type": "object", "properties": {}}),
    Tool(name="apply_plan",
         description=("Build a Resolve timeline from a text plan in one call: shots "
                      "{clip, src_in, frames|beats}, optional beat_grid, sfx [{frame, tag, dur_s}], "
                      "optional camera_bed and music. dry_run validates without touching Resolve."),
         inputSchema={"type": "object",
                      "properties": {"plan": {"type": "object"},
                                     "dry_run": {"type": "boolean", "default": False}},
                      "required": ["plan"]}),
    Tool(name="apply_recipe",
         description=("Build a timeline from a recipe: name, shots [{section, clip, src_in}], "
                      "optional timeline name, camera_bed, music. dry_run validates only."),
         inputSchema={"type": "object",
                      "properties": {"name": {"type": "string"},
                                     "shots": {"type": "array", "items": {"type": "object"}},
                                     "timeline": {"type": "string"},
                                     "camera_bed": {"type": "string"},
                                     "music": {"type": "string"},
                                     "dry_run": {"type": "boolean", "default": False}},
                      "required": ["name", "shots"]}),
]


@server.list_tools()
async def list_tools():
    return TOOLS


@server.call_tool()
async def call_tool(name, arguments):
    if name == "probe":
        path = arguments["path"]
        facts = probe_file(path)
        if facts["audio"]:
            facts["audio_mean_db"], facts["audio_peak_db"] = loudness(path)
        return [TextContent(type="text", text=json.dumps(facts, ensure_ascii=False))]

    if name == "index_media":
        step = float(arguments.get("step_s") or 1.0)
        items = [index_one(path, step) for path in arguments["paths"]]
        header = f"indexed {len(items)} files (step {step}s, cache {FILE_CACHE})"
        return [TextContent(type="text", text="\n".join([header] + [format_index(i) for i in items]))]

    if name == "audio_index":
        window = float(arguments.get("window_s") or 0.5)
        return [TextContent(type="text",
                            text=json.dumps(audio_index.summarize(arguments["path"], window),
                                            ensure_ascii=False))]

    if name == "transcribe":
        result = audio_index.transcribe(arguments["path"],
                                        size=arguments.get("size") or "tiny",
                                        language=arguments.get("language"),
                                        allow_download=bool(arguments.get("allow_download")))
        if not result.get("ok"):
            return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]
        text = "\n".join(f'[{s["start"]:6.2f}-{s["end"]:6.2f}] {s["text"]}' for s in result["segments"])
        return [TextContent(type="text", text=text or "(no speech found)")]

    if name == "contact_sheet":
        path = arguments["path"]
        start, end = float(arguments["start_s"]), float(arguments["end_s"])
        cols, rows = int(arguments.get("cols") or 4), int(arguments.get("rows") or 4)
        width = int(arguments.get("width") or 180)
        frames, tile_h = frames_rgb(path, start, end, min(cols * rows, MAX_SHEET_FRAMES), width)
        canvas = mosaic(frames, width, tile_h, cols, rows)
        times = [round(start + (end - start) * i / max(len(frames) - 1, 1), 2) for i in range(len(frames))]
        return image_tool(canvas, f"{os.path.basename(path)} {start}-{end}s, {cols}x{rows}, "
                                  f"row-major times: {times}")

    if name == "timeline_report":
        resolve = load_resolve()
        if resolve is None:
            return [TextContent(type="text", text=NO_RESOLVE)]
        project = resolve.GetProjectManager().GetCurrentProject()
        timeline = project.GetCurrentTimeline()
        if timeline is None:
            return [TextContent(type="text", text="no current timeline")]
        fps = float(project.GetSetting("timelineFrameRate") or 25)
        lines = [f'timeline "{timeline.GetName()}" {timeline.GetEndFrame()} frames @ {fps:g} fps = '
                 f'{timeline.GetEndFrame() / fps:.2f}s']
        for track in range(1, timeline.GetTrackCount("video") + 1):
            items = list(timeline.GetItemsInTrack("video", track).values())
            lines.append(f"V{track}: {len(items)} clips")
            for item in items:
                lines.append(f'  {item.GetStart():5d}-{item.GetEnd():5d} {item.GetName()} '
                             f'src {item.GetSourceStartFrame()}-{item.GetSourceEndFrame()}')
        for track in range(1, timeline.GetTrackCount("audio") + 1):
            items = list(timeline.GetItemsInTrack("audio", track).values())
            if items:
                lines.append(f"A{track}: {len(items)} clips "
                             + ", ".join(f"{i.GetName()}@{i.GetStart()}" for i in items[:6]))
        return [TextContent(type="text", text="\n".join(lines))]

    if name == "timeline_storyboard":
        resolve = load_resolve()
        if resolve is None:
            return [TextContent(type="text", text=NO_RESOLVE)]
        project = resolve.GetProjectManager().GetCurrentProject()
        timeline = project.GetCurrentTimeline()
        if timeline is None:
            return [TextContent(type="text", text="no current timeline")]
        cols, rows = int(arguments.get("cols") or 5), int(arguments.get("rows") or 4)
        width = int(arguments.get("width") or 160)
        track = int(arguments.get("track") or 1)
        items = sorted(timeline.GetItemsInTrack("video", track).values(), key=lambda i: i.GetStart())
        frames, mapping, tile_h = [], [], None
        for item in items[: cols * rows]:
            pool_item = item.GetMediaPoolItem()
            path = pool_item.GetClipProperty("File Path") if pool_item else None
            if not path:
                continue
            mid = (item.GetSourceStartFrame() + item.GetSourceEndFrame()) / 2 / 50
            got, tile_h = frames_rgb(path, mid, mid + 0.001, 1, width)
            if got:
                frames.append(got[0])
                mapping.append(f"{item.GetStart()}-{item.GetEnd()} {item.GetName()}")
        if not frames:
            return [TextContent(type="text", text="could not decode any shot")]
        return image_tool(mosaic(frames, width, tile_h, cols, rows),
                          "shots row-major: " + " | ".join(mapping))

    if name == "sfx_tags":
        tags = sfx_library.all_tags()
        lines = [f"{tag}: {meta['examples']} {meta['offset_db']:+d} dB" for tag, meta in tags.items()]
        return [TextContent(type="text", text="\n".join(lines))]

    if name == "list_recipes":
        listing = recipes.list_recipes()
        if not listing.get("ok"):
            return [TextContent(type="text", text=listing["reason"])]
        lines = []
        for recipe_name, meta in listing["recipes"].items():
            lines.append(f'{recipe_name} — {meta["title"]} ({meta["beat_grid"]["fps"]:g} fps, '
                         f'{meta["beat_grid"]["bpm"]:g} bpm)')
            for section in meta["sections"]:
                lines.append(f'  {section["id"]:9s} {section["beats"]:>2} beats  '
                             f'{section["purpose"]}  sfx={",".join(section["sfx"]) or "-"}')
        return [TextContent(type="text", text="\n".join(lines))]

    if name == "apply_plan":
        plan, warnings = planner.normalize_plan(arguments["plan"])
        dry = bool(arguments.get("dry_run"))
        result = planner.execute_plan(plan, dry_run=dry)
        if warnings:
            result["warnings"] = warnings
        if dry:
            result["shots"] = [{"clip": s["clip"], "src_in": s["src_in"],
                                "frames": s["frames"], "start": s["start"]} for s in plan["shots"]]
            result["sfx"] = [{"frame": c["frame"], "tag": c["tag"], "name": c["name"]} for c in plan["sfx"]]
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]

    if name == "apply_recipe":
        plan = recipes.expand(arguments["name"], arguments["shots"])
        plan["timeline"] = arguments.get("timeline") or f'recipe {arguments["name"]}'
        plan["camera_bed"] = arguments.get("camera_bed")
        plan["music"] = arguments.get("music")
        normalized, warnings = planner.normalize_plan(plan)
        result = planner.execute_plan(normalized, dry_run=bool(arguments.get("dry_run")))
        if warnings:
            result["warnings"] = warnings
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]

    raise ValueError(f"unknown tool {name}")


async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
