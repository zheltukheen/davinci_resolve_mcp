#!/usr/bin/env python3
"""Resolve-native automation actions: captions, loudness, silence, reframe, markers, QC.

Every function takes plain data in and returns plain data out, so the MCP layer
stays thin and the behaviour is testable without a model in the loop.
"""
import json
import os
import re
import subprocess
import sys
import time

import config

RESOLVE_MODULES = config.resolve_modules()
NO_RESOLVE = "DaVinci Resolve is not running: start it and open the project, then call this again."


def load_resolve():
    if RESOLVE_MODULES and RESOLVE_MODULES not in sys.path:
        sys.path.append(RESOLVE_MODULES)
    try:
        import DaVinciResolveScript as dvr
    except ImportError:
        return None
    return dvr.scriptapp("Resolve")


def current():
    """Return (resolve, project, timeline) or (None, None, None)."""
    resolve = load_resolve()
    if resolve is None:
        return None, None, None
    project = resolve.GetProjectManager().GetCurrentProject()
    return resolve, project, (project.GetCurrentTimeline() if project else None)


def wait_for(getter, timeout_s=180, interval_s=1.0):
    """Poll a status callable until its 'status'/completion looks finished."""
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        last = getter() or {}
        if not isinstance(last, dict):
            return last
        status = str(last.get("status") or last.get("JobStatus") or "").lower()
        if any(word in status for word in ("complete", "done", "failed", "cancel")):
            return last
        progress = last.get("completionPercentage", last.get("CompletionPercentage"))
        if progress is None and not status:
            return last
        if isinstance(progress, (int, float)) and progress >= 100:
            return last
        time.sleep(interval_s)
    return last or {"error": "timeout"}


# ---------------------------------------------------------------- captions
def _subtitle_text(item):
    for key in ("Text", "Subtitle", "Name", "Caption"):
        value = item.GetProperty(key)
        if value:
            return str(value)
    return ""


def _srt_timestamp(frame, fps):
    seconds = frame / fps
    hours, rest = divmod(seconds, 3600)
    minutes, rest = divmod(rest, 60)
    whole = int(rest)
    millis = int(round((rest - whole) * 1000))
    return f"{int(hours):02d}:{int(minutes):02d}:{whole:02d},{millis:03d}"


def captions(language="auto", chars_per_line=32, preset=None, srt_path=None,
             timeout_s=300):
    """Create subtitles from the timeline audio and write an SRT next to it."""
    resolve, project, timeline = current()
    if resolve is None:
        return {"ok": False, "reason": NO_RESOLVE}
    settings = {"charsPerLine": int(chars_per_line)}
    if language and language != "auto":
        settings["language"] = language
    if preset:
        settings["captionPreset"] = preset

    started = timeline.CreateSubtitlesFromAudio(settings)
    if not started:
        return {"ok": False,
                "reason": "CreateSubtitlesFromAudio returned False",
                "hint": ("Resolve needs the subtitle language models installed: "
                         "Preferences > System > Subtitles (or Preferences > Subtitles), "
                         "download the language, reopen the project. Also needs a subtitle track "
                         "and the timeline to be current.")}
    status = wait_for(lambda: project.GetCreateSubtitlesFromAudioStatus(), timeout_s=timeout_s)

    fps = float(project.GetSetting("timelineFrameRate") or 25)
    tracks = timeline.GetTrackCount("subtitle") or 0
    cues = []
    for track in range(1, tracks + 1):
        for item in timeline.GetItemsInTrack("subtitle", track).values():
            cues.append({"start": item.GetStart(), "end": item.GetEnd(),
                         "text": _subtitle_text(item)})
    cues.sort(key=lambda c: c["start"])

    srt_path = srt_path or os.path.join(os.path.expanduser("~"), "Desktop",
                                        f"{timeline.GetName()}.srt")
    if cues and srt_path:
        blocks = []
        for index, cue in enumerate(cues, 1):
            blocks.append(f"{index}\n{_srt_timestamp(cue['start'], fps)} --> "
                          f"{_srt_timestamp(cue['end'], fps)}\n{cue['text']}\n")
        os.makedirs(os.path.dirname(srt_path), exist_ok=True)
        with open(srt_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(blocks))

    over = [c for c in cues if len(c["text"]) > chars_per_line * 2]
    fast = [c for c in cues if c["end"] > c["start"] and
            len(c["text"]) / max((c["end"] - c["start"]) / fps, 1e-6) > 22]
    return {"ok": True, "status": status, "subtitle_tracks": tracks, "cues": len(cues),
            "srt": srt_path if cues else None,
            "too_long": len(over), "too_fast": len(fast),
            "sample": cues[:3]}


# ---------------------------------------------------------------- audio
def normalize_loudness(target_lufs=-14.0, mode=None, track="audio"):
    """Normalize every audio clip of the timeline to a loudness target."""
    resolve, project, timeline = current()
    if resolve is None:
        return {"ok": False, "reason": NO_RESOLVE}
    items = []
    for index in range(1, timeline.GetTrackCount(track) + 1):
        items += list(timeline.GetItemsInTrack(track, index).values())
    if not items:
        return {"ok": False, "reason": "no audio items"}
    modes = timeline.GetNormalizeAudioModes() if hasattr(timeline, "GetNormalizeAudioModes") else []
    options = {"targetLoudness": float(target_lufs)}
    if mode and modes and mode in modes:
        options["normalizationMode"] = mode
    ok = timeline.NormalizeAudioLevel(items, options)
    return {"ok": bool(ok), "items": len(items), "target_lufs": target_lufs,
            "modes": modes, "applied_mode": options.get("normalizationMode", "default")}


def remove_silence(threshold_db=-30.0, minimum_frames=None, crossfade=True, track="audio"):
    """Ripple-delete pauses on every clip of an audio track."""
    resolve, project, timeline = current()
    if resolve is None:
        return {"ok": False, "reason": NO_RESOLVE}
    items = list(timeline.GetItemsInTrack(track, 1).values())
    if not items:
        return {"ok": False, "reason": f"nothing on {track} track 1"}
    options = {"threshold": float(threshold_db), "crossFadeAudio": bool(crossfade)}
    if minimum_frames:
        options["minimumToStrip"] = int(minimum_frames)
    done = sum(1 for item in items if item.RippleDeleteSilence(options))
    return {"ok": done > 0, "clips": done, "of": len(items), "options": options}


def smart_reframe(track="video", track_index=1):
    """Run Resolve's SmartReframe on a video track (frame the subject for 9:16)."""
    resolve, project, timeline = current()
    if resolve is None:
        return {"ok": False, "reason": NO_RESOLVE}
    items = list(timeline.GetItemsInTrack(track, track_index).values())
    if not items:
        return {"ok": False, "reason": f"nothing on {track} {track_index}"}
    done = sum(1 for item in items if item.SmartReframe())
    # the status callable lives on Project, not on Timeline
    getter = getattr(project, "GetSmartReframeStatus", None)
    status = wait_for(getter, timeout_s=120) if getter else None
    return {"ok": done > 0, "reframed": done, "of": len(items), "status": status}


# ---------------------------------------------------------------- markers
def add_markers(markers):
    """Place review markers: [{frame, name, note, color, duration}]."""
    resolve, project, timeline = current()
    if resolve is None:
        return {"ok": False, "reason": NO_RESOLVE}
    added = 0
    for marker in markers or []:
        ok = timeline.AddMarker(int(marker["frame"]), marker.get("color") or "Blue",
                                marker.get("name") or "", marker.get("note") or "",
                                int(marker.get("duration") or 1))
        added += 1 if ok else 0
    return {"ok": added > 0, "added": added, "of": len(markers or [])}


def list_markers():
    resolve, project, timeline = current()
    if resolve is None:
        return {"ok": False, "reason": NO_RESOLVE}
    markers = timeline.GetMarkers() or {}
    return {"ok": True, "count": len(markers),
            "markers": [{"frame": frame, **value} for frame, value in sorted(markers.items())]}


def clear_markers(color="All"):
    resolve, project, timeline = current()
    if resolve is None:
        return {"ok": False, "reason": NO_RESOLVE}
    return {"ok": bool(timeline.DeleteMarkersByColor(color)), "color": color}


# ---------------------------------------------------------------- QC
def timeline_qc():
    """Structural checks on the open timeline; text only."""
    resolve, project, timeline = current()
    if resolve is None:
        return {"ok": False, "reason": NO_RESOLVE}
    fps = float(project.GetSetting("timelineFrameRate") or 25)
    end = timeline.GetEndFrame()
    report = {"timeline": timeline.GetName(), "frames": end,
              "seconds": round(end / fps, 2), "fps": fps, "problems": []}

    for track in range(1, timeline.GetTrackCount("video") + 1):
        items = sorted(timeline.GetItemsInTrack("video", track).values(),
                       key=lambda i: i.GetStart())
        covered = 0
        for previous, item in zip(items, items[1:]):
            if item.GetStart() > previous.GetEnd():
                report["problems"].append(
                    f"V{track} gap {previous.GetEnd()}-{item.GetStart()} "
                    f"({(item.GetStart() - previous.GetEnd()) / fps:.2f}s black)")
        if items:
            if items[0].GetStart() > 0:
                report["problems"].append(f"V{track} starts at {items[0].GetStart()}, not 0")
            if items[-1].GetEnd() < end:
                report["problems"].append(
                    f"V{track} ends at {items[-1].GetEnd()} before timeline end {end}")
            for item in items:
                covered += item.GetEnd() - item.GetStart()
            report[f"v{track}_clips"] = len(items)
        report[f"v{track}_covered_s"] = round(covered / fps, 2)

    audio = 0
    for track in range(1, timeline.GetTrackCount("audio") + 1):
        audio += len(timeline.GetItemsInTrack("audio", track))
    report["audio_clips"] = audio
    report["markers"] = len(timeline.GetMarkers() or {})
    report["ok"] = not report["problems"]
    return report


def qc_media(path):
    """Measure a rendered file: loudness, true peak, black frames, silence, stream facts."""
    if not os.path.exists(path):
        return {"ok": False, "reason": f"no such file: {path}"}
    info = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
        capture_output=True, check=True).stdout)
    video = next((s for s in info["streams"] if s["codec_type"] == "video"), {})
    audio = next((s for s in info["streams"] if s["codec_type"] == "audio"), {})

    loud = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-af",
                           "ebur128=peak=true", "-f", "null", "-"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    integrated = re.findall(r"I:\s*(-?[\d.]+) LUFS", loud)
    peak = re.findall(r"Peak:\s*(-?[\d.]+) dBFS", loud)
    blacks = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-vf",
                             "blackdetect=d=0.1:pix_th=0.10", "-f", "null", "-"],
                            capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    black_spans = re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", blacks)
    silences = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-af",
                               "silencedetect=noise=-40dB:d=1", "-f", "null", "-"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    silence_spans = re.findall(r"silence_start: ([\d.]+)", silences)

    duration = float(info["format"].get("duration") or 0)
    problems = []
    if integrated and float(integrated[-1]) < -18:
        problems.append(f"quiet: {integrated[-1]} LUFS (target around -14)")
    if integrated and float(integrated[-1]) > -10:
        problems.append(f"hot: {integrated[-1]} LUFS")
    if peak and float(peak[-1]) > -0.5:
        problems.append(f"peak {peak[-1]} dBFS risks clipping")
    if black_spans:
        problems.append(f"{len(black_spans)} black span(s) >= 0.1s")
    if audio and not integrated:
        problems.append("audio stream present but loudness unreadable")

    return {"ok": not problems, "file": os.path.basename(path),
            "duration_s": round(duration, 2),
            "video": f'{video.get("width")}x{video.get("height")} {video.get("codec_name")} '
                     f'{(video.get("r_frame_rate") or "")}',
            "audio": f'{audio.get("channels", 0)}ch {(audio.get("sample_rate") or "")}',
            "lufs": float(integrated[-1]) if integrated else None,
            "true_peak_dbfs": float(peak[-1]) if peak else None,
            "black_spans": len(black_spans), "silence_starts": len(silence_spans),
            "problems": problems}


if __name__ == "__main__":
    print(json.dumps({"timeline": timeline_qc()}, ensure_ascii=False, indent=1)[:800])
