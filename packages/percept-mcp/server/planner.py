#!/usr/bin/env python3
"""Deterministic edit-plan executor for DaVinci Resolve.

A plan is text. One call builds the timeline: shots on V1, semantic SFX cues baked
and placed on their own tracks, optionally a music file. Nothing interactive.
"""
import hashlib
import json
import os
import subprocess
import sys
import wave

import numpy as np

import sfx_library

import config

RESOLVE_MODULES = config.resolve_modules()
CACHE = os.path.join(config.CACHE_DIR, "sfx")
DEFAULT_BED_DB = -29.0     # camera ambience of the user's phone footage
CUE_PEAK_LIMIT = -4.4      # dBFS per cue, keeps the sum clean
SR = 48000


# ---------------------------------------------------------------- plan handling
def beat_frames(fps, bpm, beats):
    return int(round(beats * fps * 60.0 / bpm))


def normalize_plan(plan):
    """Validate a plan and turn beat counts into frame counts. Returns (plan, warnings)."""
    warnings = []
    fps = float(plan.get("fps") or 50)
    bpm = float((plan.get("beat_grid") or {}).get("bpm") or 0)
    shots = []
    start_frame = int(plan.get("start_frame") or 0)
    cursor = start_frame
    frames_per_beat = fps * 60.0 / bpm if bpm else 0.0
    beats_used = 0.0
    for index, shot in enumerate(plan.get("shots") or []):
        if "clip" not in shot:
            raise ValueError(f"shot {index} has no clip")
        if shot.get("frames"):
            frames = int(shot["frames"])
            start, end = cursor, cursor + frames
        elif shot.get("beats") and frames_per_beat:
            # bounds come from the cumulative beat position, so rounding cannot drift
            start = start_frame + round(beats_used * frames_per_beat)
            beats_used += float(shot["beats"])
            end = start_frame + round(beats_used * frames_per_beat)
            frames = end - start
        else:
            raise ValueError(f"shot {index} needs frames or beats+beat_grid")
        src_in = int(shot.get("src_in") or 0)
        shots.append({"clip": shot["clip"], "src_in": src_in, "frames": frames,
                      "start": start, "end": end, "slot": shot.get("slot")})
        cursor = end
    if not shots:
        raise ValueError("plan has no shots")

    if bpm:
        beat = fps * 60.0 / bpm
        for shot in shots:
            drift = shot["start"] - round(shot["start"] / beat) * beat
            if abs(drift) > 0.5:
                warnings.append(f'shot at {shot["start"]} is {drift:+.1f} frames off the beat grid')

    cues = []
    seen = {}
    for index, cue in enumerate(plan.get("sfx") or []):
        tag = cue.get("tag")
        if not tag:
            raise ValueError(f"sfx {index} has no tag")
        # rotate through the tag's files so six knife hits are not the same sample
        variant = cue.get("variant")
        if variant is None:
            variant = seen.get(tag, 0)
            seen[tag] = variant + 1
        item = sfx_library.resolve(tag, int(variant))
        cues.append({
            "frame": int(cue["frame"]),
            "tag": tag,
            "name": item["name"],
            "path": item["path"],
            "dur_s": float(cue.get("dur_s") or 0),
            "offset_db": float(cue.get("offset_db", item["offset_db"])),
        })
    cues.sort(key=lambda c: c["frame"])

    return {
        "timeline": plan.get("timeline") or "Plan timeline",
        "fps": fps,
        "bpm": bpm or None,
        "shots": shots,
        "sfx": cues,
        "music": plan.get("music"),
        "camera_bed": plan.get("camera_bed"),
        "bed_db": float(plan.get("bed_db") or DEFAULT_BED_DB),
        "total_frames": shots[-1]["end"],
    }, warnings


# ---------------------------------------------------------------- cue audio
def _cue_energy(path, dur_s):
    cmd = ["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(SR),
           "-t", f"{dur_s:.3f}", "-f", "f32le", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    data = np.frombuffer(raw, dtype="<f4").astype(np.float64)
    if not len(data):
        raise ValueError(f"no audio decoded from {path}")
    return data


def bake_cue(cue, bed_db):
    """Pick the loudest window, set gain relative to the bed, write one wav."""
    os.makedirs(CACHE, exist_ok=True)
    key = hashlib.sha1(json.dumps([cue["path"], cue["offset_db"], bed_db, cue["dur_s"]],
                                 sort_keys=True).encode()).hexdigest()[:16]
    out = os.path.join(CACHE, f'{cue["frame"]:05d}_{cue["tag"]}_{key}.wav')
    if os.path.exists(out):
        return out, _gain_report(cue, out)

    data = _cue_energy(cue["path"], cue["dur_s"] or 60.0)
    n = int(SR * (cue["dur_s"] or len(data) / SR))
    data = data[:n]
    if len(data) < n:  # pad so the cue keeps its planned length
        data = np.pad(data, (0, n - len(data)))
    fi, fo = int(0.002 * SR), int(0.02 * SR)
    if len(data) > 2 * fi:
        data[:fi] *= np.linspace(0, 1, fi)
    if len(data) > 2 * fo:
        data[-fo:] *= np.linspace(1, 0, fo)
    rms = float(np.sqrt((data ** 2).mean()))
    gain = (bed_db + cue["offset_db"]) - 20 * np.log10(max(rms, 1e-9))
    peak = float(np.abs(data).max()) * 10 ** (gain / 20)
    if peak > 10 ** (CUE_PEAK_LIMIT / 20):
        gain -= 20 * np.log10(peak / 10 ** (CUE_PEAK_LIMIT / 20))
    data = data * 10 ** (gain / 20)
    stereo = np.stack([data, data], axis=1)
    ints = (np.clip(stereo, -1, 1) * (2 ** 23 - 1)).astype("<i4")
    raw = ints.view(np.uint8).reshape(-1, 4)[:, :3].tobytes()
    with wave.open(out, "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(3)
        handle.setframerate(SR)
        handle.writeframes(raw)
    return out, _gain_report(cue, out)


def _gain_report(cue, path):
    with wave.open(path, "rb") as handle:
        length = handle.getnframes() / handle.getframerate()
    return {"file": path, "frames": round(length * 50), "name": cue["name"]}


# ---------------------------------------------------------------- resolve side
def _ensure_modules():
    if RESOLVE_MODULES and RESOLVE_MODULES not in sys.path:
        sys.path.append(RESOLVE_MODULES)


def load_resolve():
    _ensure_modules()
    try:
        import DaVinciResolveScript as dvr
    except ImportError:
        return None
    return dvr.scriptapp("Resolve")


def walk_folder(folder):
    for clip in folder.GetClipList():
        yield clip
    for sub in folder.GetSubFolderList():
        yield from walk_folder(sub)


def find_clip(pool, name):
    for clip in walk_folder(pool.GetRootFolder()):
        if clip.GetName() == name:
            return clip
    return None


def execute_plan(plan, dry_run=False):
    resolve = load_resolve()
    if resolve is None:
        return {"ok": False, "reason": "DaVinci Resolve is not running"}
    project = resolve.GetProjectManager().GetCurrentProject()
    pool = project.GetMediaPool()

    missing = [shot["clip"] for shot in plan["shots"] if find_clip(pool, shot["clip"]) is None]
    if missing:
        return {"ok": False, "reason": f"clips not in the media pool: {sorted(set(missing))}"}

    cues = [bake_cue(cue, plan["bed_db"]) for cue in plan["sfx"]] if not dry_run else []
    if dry_run:
        return {"ok": True, "dry_run": True,
                "timeline": plan["timeline"],
                "total_frames": plan["total_frames"],
                "shots": plan["shots"], "sfx": plan["sfx"]}

    for index in range(1, project.GetTimelineCount() + 1):
        existing = project.GetTimelineByIndex(index)
        if existing.GetName() == plan["timeline"]:
            pool.DeleteTimelines([existing])
            break
    timeline = pool.CreateEmptyTimeline(plan["timeline"])
    if timeline is None:
        return {"ok": False, "reason": "could not create the timeline"}
    project.SetCurrentTimeline(timeline)
    while timeline.GetTrackCount("audio") < 4:
        timeline.AddTrack("audio")

    for shot in plan["shots"]:
        item = find_clip(pool, shot["clip"])
        made = pool.AppendToTimeline([{
            "mediaPoolItem": item, "startFrame": shot["src_in"],
            "endFrame": shot["src_in"] + shot["frames"], "mediaType": 1, "trackIndex": 1}])
        if not made:
            return {"ok": False, "reason": f'shot {shot["clip"]} did not append'}

    if plan.get("camera_bed"):
        bed = find_clip(pool, os.path.basename(plan["camera_bed"]))
        if bed is None:
            imported = resolve.GetMediaStorage().AddItemListToMediaPool([plan["camera_bed"]])
            bed = imported[0] if imported else find_clip(pool, os.path.basename(plan["camera_bed"]))
        if bed:
            with wave.open(plan["camera_bed"], "rb") as handle:
                bed_frames = round(handle.getnframes() / handle.getframerate() * plan["fps"])
            pool.AppendToTimeline([{"mediaPoolItem": bed, "startFrame": 0,
                                    "endFrame": min(bed_frames, plan["total_frames"]),
                                    "mediaType": 2, "trackIndex": 1, "recordFrame": 0}])

    if plan.get("music"):
        music = find_clip(pool, os.path.basename(plan["music"]))
        if music is None:
            imported = resolve.GetMediaStorage().AddItemListToMediaPool([plan["music"]])
            music = imported[0] if imported else None
        if music:
            pool.AppendToTimeline([{"mediaPoolItem": music, "startFrame": 0,
                                    "endFrame": plan["total_frames"], "mediaType": 2,
                                    "trackIndex": 2, "recordFrame": 0}])

    track_end, placed = {}, []
    for cue, (path, info) in zip(plan["sfx"], cues):
        clip_name = os.path.basename(path)
        item = find_clip(pool, clip_name)
        if item is None:
            imported = resolve.GetMediaStorage().AddItemListToMediaPool([path])
            item = find_clip(pool, clip_name)
        if item is None:
            continue
        length = info["frames"]
        track = next((t for t in range(3, timeline.GetTrackCount("audio") + 1)
                      if track_end.get(t, 0) <= cue["frame"]), None)
        if track is None:
            timeline.AddTrack("audio")
            track = timeline.GetTrackCount("audio")
        pool.AppendToTimeline([{"mediaPoolItem": item, "startFrame": 0, "endFrame": length,
                                "mediaType": 2, "trackIndex": track,
                                "recordFrame": cue["frame"]}])
        track_end[track] = cue["frame"] + length
        placed.append({"frame": cue["frame"], "tag": cue["tag"], "name": info["name"]})

    resolve.GetProjectManager().SaveProject()
    return {"ok": True, "timeline": plan["timeline"],
            "total_frames": plan["total_frames"],
            "seconds": round(plan["total_frames"] / plan["fps"], 2),
            "shots": len(plan["shots"]), "sfx": len(placed),
            "tracks": timeline.GetTrackCount("audio")}
