#!/usr/bin/env python3
"""Audio-first analysis: loudness windows, silences, speech-likeness and optional ASR.

Editing decisions are usually cheaper and better from audio than from pictures:
where someone talks, where the pan sizzles, where it goes quiet. All of it is text.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile

import numpy as np

SR = 16000


def _decode(path, band=None):
    cmd = ["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(SR),
           "-f", "f32le", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype="<f4").astype(np.float64)


def _band_ratio(block, lo=300, hi=3400):
    spec = np.abs(np.fft.rfft(block)) ** 2
    freqs = np.fft.rfftfreq(len(block), 1 / SR)
    total = spec.sum()
    if total <= 0:
        return 0.0
    return float(spec[(freqs >= lo) & (freqs <= hi)].sum() / total)


def windows(path, window_s=0.5):
    samples = _decode(path)
    step = int(window_s * SR)
    rows = []
    for start in range(0, len(samples) - step + 1, step):
        block = samples[start:start + step]
        rms = float(np.sqrt((block ** 2).mean()))
        rows.append({
            "t": round(start / SR, 2),
            "rms_db": round(20 * np.log10(max(rms, 1e-9)), 1),
            "peak_db": round(20 * np.log10(max(float(np.abs(block).max()), 1e-9)), 1),
            "voice": round(_band_ratio(block), 2),
        })
    return rows


def silences(path, threshold_db=-35, min_s=0.4):
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", path, "-af",
         f"silencedetect=noise={threshold_db}dB:d={min_s}", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", out)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", out)]
    return [{"start": round(s, 2), "end": round(e, 2)} for s, e in zip(starts, ends)]


def speech_runs(rows, min_rms_db=-42, min_voice=0.55, min_windows=2):
    """Contiguous windows that look like talking rather than machinery."""
    runs, current = [], []
    for row in rows:
        looks_spoken = row["rms_db"] > min_rms_db and row["voice"] >= min_voice
        if looks_spoken:
            current.append(row)
        elif current:
            if len(current) >= min_windows:
                runs.append({"start": current[0]["t"], "end": current[-1]["t"]})
            current = []
    if len(current) >= min_windows:
        runs.append({"start": current[0]["t"], "end": current[-1]["t"]})
    return runs


# ---------------------------------------------------------------- ASR
import config

MODEL_DIR = os.environ.get("PERCEPT_MODEL_DIR", os.path.join(config.CACHE_DIR, "models"))
MODEL_URL = {
    "tiny": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.bin",
    "base": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin",
}


def whisper_binary():
    for name in ("whisper-cli", "whisper-cpp", "main"):
        found = shutil.which(name)
        if found:
            return found
    return None


def model_path(size="tiny"):
    return os.path.join(MODEL_DIR, f"ggml-{size}.bin")


def ensure_model(size="tiny", allow_download=False):
    path = model_path(size)
    if os.path.exists(path):
        return path
    if not allow_download:
        return None
    os.makedirs(MODEL_DIR, exist_ok=True)
    url = MODEL_URL.get(size)
    if not url:
        raise ValueError(f"no download url for model size {size}")
    subprocess.run(["curl", "-sSL", "--max-time", "600", "-o", path, url], check=True)
    return path


def transcribe(path, size="tiny", language=None, allow_download=False, timeout=600):
    binary = whisper_binary()
    if not binary:
        return {"ok": False, "reason": "whisper-cli is not installed"}
    model = ensure_model(size, allow_download)
    if not model:
        return {"ok": False, "reason": f"model ggml-{size}.bin missing",
                "hint": f"call transcribe again with allow_download=true (~{'75' if size == 'tiny' else '142'} MB)"}
    with tempfile.TemporaryDirectory() as tmp:
        base = os.path.join(tmp, "out")
        # whisper.cpp reads wav/mp3/ogg/flac only, so pull the audio out first
        wav = os.path.join(tmp, "audio.wav")
        subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1",
                        "-ar", str(SR), "-f", "wav", wav], check=True)
        cmd = [binary, "-m", model, "-f", wav, "-oj", "-of", base, "-nt"]
        if language:
            cmd += ["-l", language]
        run = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        if run.returncode != 0:
            return {"ok": False, "reason": (run.stderr or run.stdout)[-400:]}
        data = json.load(open(base + ".json", encoding="utf-8"))
        segments = [{"start": round(s["offsets"]["from"] / 1000, 2),
                     "end": round(s["offsets"]["to"] / 1000, 2),
                     "text": s["text"].strip()} for s in data.get("transcription", [])]
        return {"ok": True, "segments": segments, "model": size}


def summarize(path, window_s=0.5):
    rows = windows(path, window_s)
    if not rows:
        return {"file": os.path.basename(path), "reason": "no audio"}
    rms = [row["rms_db"] for row in rows]
    voice = [row["voice"] for row in rows]
    loudest = max(rows, key=lambda r: r["rms_db"])
    return {
        "file": os.path.basename(path),
        "windows": len(rows),
        "rms_db": [round(float(np.mean(rms)), 1), round(float(np.min(rms)), 1), round(float(np.max(rms)), 1)],
        "voice_ratio": [round(float(np.mean(voice)), 2), round(float(np.max(voice)), 2)],
        "loudest_at_s": loudest["t"],
        "speech_runs": speech_runs(rows),
        "silences": silences(path)[:12],
    }


if __name__ == "__main__":
    import sys
    for target in sys.argv[1:]:
        print(json.dumps(summarize(target), ensure_ascii=False, indent=1))
