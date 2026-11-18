#!/usr/bin/env python3
"""Transcript-, music- and multicam-level automation.

Everything here works from the same premise as the rest of the server: compute
locally, return text, and let the caller spend words only on decisions.
"""
import hashlib
import json
import os
import subprocess
import sys

import numpy as np

import audio_index

CACHE = os.path.expanduser("~/.cache/percept")
TRANSCRIPTS = os.path.join(CACHE, "transcripts")
MUSIC = os.path.join(CACHE, "music")
RESOLVE_MODULES = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules"
SR = 16000


# ---------------------------------------------------------------- plumbing
def load_resolve():
    if RESOLVE_MODULES not in sys.path:
        sys.path.append(RESOLVE_MODULES)
    try:
        import DaVinciResolveScript as dvr
    except ImportError:
        return None
    return dvr.scriptapp("Resolve")


def project_pool():
    resolve = load_resolve()
    if resolve is None:
        return None, None, None
    project = resolve.GetProjectManager().GetCurrentProject()
    if project is None:
        return None, None, None
    return resolve, project, project.GetMediaPool()


def walk(folder):
    for clip in folder.GetClipList():
        yield clip
    for sub in folder.GetSubFolderList():
        yield from walk(sub)


def find_clip(pool, name):
    for clip in walk(pool.GetRootFolder()):
        if clip.GetName() == name:
            return clip
    return None


def cache_path(root, key, payload):
    os.makedirs(root, exist_ok=True)
    digest = hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:20]
    return os.path.join(root, f"{digest}.json")


# ---------------------------------------------------------------- transcripts
def _tc_to_seconds(value, fps=25.0):
    """Resolve returns transcription times as timecode strings, not seconds."""
    if isinstance(value, (int, float)):
        return float(value)
    parts = str(value).split(":")
    try:
        if len(parts) == 4:
            hours, minutes, seconds, frames = (float(p) for p in parts)
            return hours * 3600 + minutes * 60 + seconds + frames / fps
        if len(parts) == 3:
            hours, minutes, seconds = (float(p) for p in parts)
            return hours * 3600 + minutes * 60 + seconds
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _resolve_transcript(clip, timeout_s=180, fps=25.0):
    """Ask Resolve to transcribe; returns None when the models are missing."""
    if not hasattr(clip, "TranscribeAudio") or not hasattr(clip, "GetTranscription"):
        return None
    started = clip.TranscribeAudio()
    if not started:
        return None
    base = _tc_to_seconds(str(clip.GetClipProperty("Start TC") or "00:00:00:00"), fps)
    for _ in range(int(timeout_s / 2)):
        data = clip.GetTranscription() or {}
        segments = data.get("segments") if isinstance(data, dict) else None
        if segments:
            return [{"start": round(max(_tc_to_seconds(s.get("start"), fps) - base, 0.0), 2),
                     "end": round(max(_tc_to_seconds(s.get("end"), fps) - base, 0.0), 2),
                     "text": str(s.get("text", "")).strip()} for s in segments]
        import time
        time.sleep(2)
    return None


def transcribe_clip(name, language=None, size="tiny", use_resolve=True):
    """Transcribe one media pool clip: Resolve first, local whisper as fallback."""
    resolve, project, pool = project_pool()
    if pool is None:
        return {"ok": False, "reason": "DaVinci Resolve is not running"}
    clip = find_clip(pool, name)
    if clip is None:
        return {"ok": False, "reason": f"clip not in the media pool: {name}"}
    path = clip.GetClipProperty("File Path")
    segments, source = None, None
    fps = float(project.GetSetting("timelineFrameRate") or 25)
    if use_resolve:
        segments = _resolve_transcript(clip, fps=fps)
        source = "resolve" if segments else None
    if segments is None:
        result = audio_index.transcribe(path, size=size, language=language)
        if not result.get("ok"):
            return {"ok": False, "reason": result.get("reason"), "hint": result.get("hint")}
        segments, source = result["segments"], f"whisper-{size}"
    payload = {"clip": name, "path": path, "source": source, "segments": segments}
    json.dump(payload, open(cache_path(TRANSCRIPTS, name, [path]), "w", encoding="utf-8"),
              ensure_ascii=False)
    return {"ok": True, **payload, "words": sum(len(s["text"].split()) for s in segments)}


def transcript_index(bin_name=None, limit=8, size="tiny"):
    """Transcribe up to `limit` clips and cache them, so a search is free later."""
    resolve, project, pool = project_pool()
    if pool is None:
        return {"ok": False, "reason": "DaVinci Resolve is not running"}
    folder = pool.GetRootFolder()
    if bin_name:
        found = [f for f in walk_folders(pool.GetRootFolder()) if f.GetName() == bin_name]
        if not found:
            return {"ok": False, "reason": f"bin not found: {bin_name}"}
        folder = found[0]
    clips = [c for c in walk(folder) if str(c.GetClipProperty("Type") or "").lower().startswith("видео") or
             str(c.GetClipProperty("Type") or "").lower().startswith("video")]
    rows = []
    for clip in clips[:limit]:
        row = transcribe_clip(clip.GetName(), size=size)
        rows.append({"clip": clip.GetName(), "ok": row.get("ok"),
                     "segments": len(row.get("segments") or []), "source": row.get("source")})
    return {"ok": True, "transcribed": rows}


def walk_folders(folder):
    yield folder
    for sub in folder.GetSubFolderList():
        yield from walk_folders(sub)


def search_phrases(query, clip_names=None, limit=20):
    """Find cached transcript segments containing the query."""
    needle = query.lower().strip()
    hits = []
    if not os.path.isdir(TRANSCRIPTS):
        return hits
    for name in sorted(os.listdir(TRANSCRIPTS)):
        if not name.endswith(".json"):
            continue
        data = json.load(open(os.path.join(TRANSCRIPTS, name), encoding="utf-8"))
        if clip_names and data["clip"] not in clip_names:
            continue
        for segment in data.get("segments", []):
            if needle in segment["text"].lower():
                hits.append({"clip": data["clip"], "start": segment["start"],
                             "end": segment["end"], "text": segment["text"]})
    return hits[:limit]


def string_out(query, timeline="String-out", pad_s=0.0, camera_bed=None):
    """Cut a rough timeline from the transcript: one shot per matched phrase."""
    import planner
    hits = search_phrases(query)
    if not hits:
        return {"ok": False, "reason": f'no transcribed segment matches "{query}"',
                "hint": "run transcript_index first"}
    fps = 50.0
    shots = []
    for hit in hits:
        frames = max(int(round((hit["end"] - hit["start"] + pad_s) * fps)), int(fps / 2))
        shots.append({"clip": hit["clip"], "src_in": int(round(hit["start"] * fps)),
                      "frames": frames})
    plan, warnings = planner.normalize_plan({"timeline": timeline, "fps": fps,
                                             "shots": shots, "camera_bed": camera_bed})
    result = planner.execute_plan(plan, dry_run=True)
    result["phrases"] = [h["text"] for h in hits]
    result["timeline"] = timeline
    return result


# ---------------------------------------------------------------- b-roll
FEATURE_WORDS = {
    "bright": ("lum", "high"), "тёмн": ("lum", "low"), "dark": ("lum", "low"),
    "fast": ("motion", "high"), "быстр": ("motion", "high"),
    "calm": ("motion", "low"), "спокой": ("motion", "low"),
    "colorful": ("color", "high"), "цветн": ("color", "high"),
}


def b_roll(keywords, limit=8, min_motion=None):
    """Rank clips for b-roll: transcript hits first, then feature fit."""
    resolve, project, pool = project_pool()
    if pool is None:
        return {"ok": False, "reason": "DaVinci Resolve is not running"}
    words = [w.strip().lower() for w in (keywords or "").split() if w.strip()]
    _ensure_index.calls = 0
    clips = [c for c in walk(pool.GetRootFolder())
             if str(c.GetClipProperty("File Path") or "").endswith((".mp4", ".mov", ".mxf"))]
    scored = []
    for clip in clips:
        name = clip.GetName()
        index_path = os.path.join(CACHE, "files")
        score, reasons = 0.0, []
        transcript_hits = [h for h in search_phrases(" ".join(words)) if h["clip"] == name]
        if transcript_hits:
            score += 3 * len(transcript_hits)
            reasons.append(f"transcript: {transcript_hits[0]['text'][:60]}")
        for word, (feature, direction) in FEATURE_WORDS.items():
            if any(word in w for w in words):
                data = _ensure_index(clip.GetClipProperty("File Path"))
                if not data:
                    continue
                value = data["max_motion"] if feature == "motion" else (
                    data["lum_range"][1] - data["lum_range"][0] if feature == "lum" else 40)
                score += 1 if direction == "high" else 0.5
                reasons.append(f"{feature}={value:.0f}")
        if min_motion is not None:
            data = _ensure_index(clip.GetClipProperty("File Path"))
            if data and data["max_motion"] < float(min_motion):
                continue
        if score:
            scored.append({"clip": name, "score": round(score, 1), "reasons": reasons[:3]})
    scored.sort(key=lambda r: -r["score"])
    return {"ok": True, "query": keywords, "candidates": scored[:limit],
            "note": "transcript hits need transcript_index; feature hits need index_media"}


def _ensure_index(path, budget=6):
    """Use the cached storyboard index, indexing a few clips on demand."""
    data = _cached_index(path)
    if data:
        return data
    if _ensure_index.calls >= budget:
        return None
    _ensure_index.calls += 1
    try:
        import server as perception
        return perception.index_one(path, 1.0)
    except Exception:  # noqa: BLE001
        return None


_ensure_index.calls = 0


def _cached_index(path):
    directory = os.path.join(CACHE, "files")
    if not os.path.isdir(directory):
        return None
    try:
        stat = os.stat(path)
    except OSError:
        return None
    key = hashlib.sha1(f"{path}:{stat.st_size}:{int(stat.st_mtime)}:1.0".encode()).hexdigest()[:20]
    cached = os.path.join(directory, f"{key}.json")
    if os.path.exists(cached):
        return json.load(open(cached, encoding="utf-8"))
    return None


# ---------------------------------------------------------------- music
def _onset_envelope(samples, hop=512):
    frames = (len(samples) - 1024) // hop
    if frames <= 0:
        return np.zeros(1)
    window = np.hanning(1024)
    energy = np.array([np.abs(np.fft.rfft(samples[i * hop:i * hop + 1024] * window)).sum()
                       for i in range(frames)])
    flux = np.maximum(np.diff(energy), 0)
    return flux / (flux.max() + 1e-9)


def estimate_tempo(path, bpm_range=(60.0, 190.0)):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(SR),
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    samples = np.frombuffer(raw, dtype="<f4").astype(np.float64)
    if len(samples) < SR:
        return None, None
    envelope = _onset_envelope(samples)
    envelope = envelope - envelope.mean()
    autocorr = np.correlate(envelope, envelope, mode="full")[len(envelope) - 1:]
    fps_env = SR / 512
    low_lag = int(fps_env * 60 / bpm_range[1])
    high_lag = int(fps_env * 60 / bpm_range[0])
    if high_lag >= len(autocorr):
        return None, None
    # comb scoring: a real tempo peaks at the beat period and at its multiples,
    # which is far more stable than taking the single highest autocorrelation lag
    best_bpm, best_score = None, -1.0
    for bpm in np.arange(bpm_range[0], bpm_range[1] + 1, 0.5):
        lag = 60.0 * fps_env / bpm
        score = 0.0
        for harmonic in (1, 2, 3, 4):
            index = int(round(lag * harmonic))
            if index < len(autocorr):
                score += autocorr[index] / (autocorr[0] + 1e-9) / harmonic
        if score > best_score:
            best_bpm, best_score = float(bpm), score
    if best_bpm is None:
        return None, None

    def comb(bpm):
        lag = 60.0 * fps_env / bpm
        total = 0.0
        for harmonic in (1, 2, 3, 4):
            index = int(round(lag * harmonic))
            if index < len(autocorr):
                total += autocorr[index] / (autocorr[0] + 1e-9) / harmonic
        return total

    # fold half-tempo estimates up: 55-65 BPM is usually a 110-130 BPM track
    if best_bpm < 90 and best_bpm * 2 <= bpm_range[1]:
        doubled = best_bpm * 2
        if comb(doubled) >= 0.9 * best_score:
            best_bpm, best_score = doubled, comb(doubled)
    return round(best_bpm, 1), round(best_score / 2.0, 2)


def _loudness(path):
    import re
    out = subprocess.run(["ffmpeg", "-hide_banner", "-i", path, "-af", "volumedetect",
                          "-f", "null", "-"], capture_output=True, text=True,
                         encoding="utf-8", errors="replace").stderr
    mean = re.search(r"mean_volume: (-?[\d.]+)", out)
    peak = re.search(r"max_volume: (-?[\d.]+)", out)
    return (float(mean.group(1)) if mean else None, float(peak.group(1)) if peak else None)


def music_index(folder, limit=40):
    """BPM, loudness and length for every audio file in a folder (cached)."""
    if not os.path.isdir(folder):
        return {"ok": False, "reason": f"not a folder: {folder}"}
    files = [os.path.join(folder, f) for f in sorted(os.listdir(folder))
             if f.lower().endswith((".mp3", ".wav", ".m4a", ".flac", ".aiff", ".ogg"))][:limit]
    rows = []
    for path in files:
        cached = cache_path(MUSIC, os.path.basename(path), [path])
        if os.path.exists(cached):
            rows.append(json.load(open(cached, encoding="utf-8")))
            continue
        bpm, confidence = estimate_tempo(path)
        duration = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                                         "format=duration", "-of", "csv=p=0", path],
                                        capture_output=True, text=True,
                                        encoding="utf-8", errors="replace").stdout.strip() or 0)
        data = {"file": os.path.basename(path), "path": path, "bpm": bpm,
                "confidence": confidence, "duration_s": round(duration, 2)}
        data["mean_db"], data["peak_db"] = _loudness(path)
        json.dump(data, open(cached, "w", encoding="utf-8"), ensure_ascii=False)
        rows.append(data)
    return {"ok": True, "folder": folder, "tracks": rows}


def match_music(bpm, duration_s, folder):
    """Rank indexed tracks by tempo closeness and whether they cover the length."""
    indexed = music_index(folder)
    if not indexed.get("ok"):
        return indexed
    ranked = []
    for track in indexed["tracks"]:
        if not track.get("bpm"):
            continue
        # compare across octave folds so a half/double tempo estimate still matches
        folds = [track["bpm"], track["bpm"] * 2, track["bpm"] / 2]
        tempo_gap = min(abs(f - float(bpm)) for f in folds)
        long_enough = track["duration_s"] >= float(duration_s)
        ranked.append({**track, "tempo_gap": round(tempo_gap, 1), "long_enough": long_enough,
                       "score": round(max(0.0, 10 - tempo_gap) + (2 if long_enough else 0), 1)})
    ranked.sort(key=lambda r: -r["score"])
    return {"ok": True, "query": {"bpm": bpm, "duration_s": duration_s},
            "candidates": ranked[:8]}


# ---------------------------------------------------------------- multicam
def create_multicam(clip_names, name=None, sync="waveform"):
    """Build a multicam clip from media pool clips, synced by audio waveform."""
    resolve, project, pool = project_pool()
    if pool is None:
        return {"ok": False, "reason": "DaVinci Resolve is not running"}
    clips = [find_clip(pool, clip) for clip in clip_names]
    missing = [n for n, c in zip(clip_names, clips) if c is None]
    if missing:
        return {"ok": False, "reason": f"clips not in the media pool: {missing}"}
    mode = (resolve.MULTICAM_ANGLE_SYNC_AUDIO if sync == "waveform"
            else resolve.MULTICAM_ANGLE_SYNC_TIMECODE)
    made = pool.CreateMulticamClip(clips, {"name": name or "Multicam", "angleSyncMode": mode,
                                           "channelConfig": 1})
    if not made:
        return {"ok": False, "reason": "CreateMulticamClip refused"}
    return {"ok": True, "multicam": name or "Multicam", "angles": len(clips),
            "created": [getattr(item, "GetName", lambda: "?")() for item in made]
            if isinstance(made, list) else [getattr(made, "GetName", lambda: "?")()]}


if __name__ == "__main__":
    print(json.dumps(music_index(sys.argv[1] if len(sys.argv) > 1 else
                                 "/Users/zheltukheen/.dsh/bundles/brag/skills/brag/assets/music"),
                     ensure_ascii=False)[:800])
