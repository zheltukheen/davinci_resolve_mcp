#!/usr/bin/env python3
"""Meaning-first assembly.

String-out works from speech. Most b-roll has none, so this module assembles from a
semantic shot index instead: a JSON file whose shots carry roles ("fire", "chop",
"garnish"). The agent fills that index once from contact sheets, and afterwards a
brief like "fire, chop, sauce, hero" becomes a timeline in one call.
"""
import json
import os

import config
import planner
import recipes

# role in a brief -> recipe section that owns the sound design for it
ROLE_SECTIONS = {
    "open": "open", "fire": "flare", "grill": "close", "close": "close",
    "chop": "cut", "cut": "cut", "sauce": "prep", "prep": "prep",
    "assemble": "assemble", "fill": "assemble", "garnish": "garnish",
    "hero": "hero", "finish": "hero",
}


def tags_path(path=None):
    if path:
        return path
    env = os.environ.get("PERCEPT_TAGS")
    if env:
        return env
    return os.path.join(config.BUNDLE_DIR, "examples", "shot-tags.example.json")


def load_tags(path=None):
    candidate = tags_path(path)
    if not os.path.exists(candidate):
        return None, f"shot tags not found: {candidate}"
    data = json.load(open(candidate, encoding="utf-8"))
    shots = data.get("shots") or data if isinstance(data, list) else data.get("shots")
    if not shots:
        return None, "shot tags file has no shots"
    return shots, None


def pick_shot(shots, used, role, want=None):
    """First unused shot whose roles contain the role; `want` adds a secondary match."""
    for shot in shots:
        if id(shot) in used:
            continue
        roles = [str(r).lower() for r in shot.get("roles", [])]
        if role in roles and (want is None or want in roles):
            used.add(id(shot))
            return shot
    for shot in shots:
        if id(shot) in used:
            continue
        if role in [str(r).lower() for r in shot.get("roles", [])]:
            used.add(id(shot))
            return shot
    return None


def parse_brief(brief):
    """Accept "fire, chop, hero" or [{role, beats}] and return [{role, beats}]."""
    if isinstance(brief, str):
        roles = [part.strip().lower() for part in brief.replace(";", ",").split(",") if part.strip()]
        return [{"role": role, "beats": 5 if role in ("hero", "finish") else 2} for role in roles]
    parsed = []
    for item in brief or []:
        if isinstance(item, str):
            role = item.strip().lower()
            parsed.append({"role": role, "beats": 5 if role in ("hero", "finish") else 2})
        else:
            role = str(item.get("role", "")).strip().lower()
            beats = int(item.get("beats") or (5 if role in ("hero", "finish") else 2))
            parsed.append({"role": role, "beats": beats, "want": item.get("want")})
    return parsed


def assemble(brief, timeline="Semantic cut", tags=None, camera_bed=None, music=None,
             dry_run=True, recipe="reel_tacos", fps=50, bpm=110):
    """Turn a brief into a plan and build it, using semantics instead of speech."""
    shots, error = load_tags(tags)
    if error:
        return {"ok": False, "reason": error}

    parsed = parse_brief(brief)
    if not parsed:
        return {"ok": False, "reason": "brief is empty"}

    used, assignments, missing = set(), [], []
    for item in parsed:
        shot = pick_shot(shots, used, item["role"], item.get("want"))
        if shot is None:
            missing.append(item["role"])
            continue
        section = ROLE_SECTIONS.get(item["role"], "prep")
        assignments.append({"section": section, "role": item["role"], "clip": shot["clip"],
                            "src_in": int(shot.get("src_in") or 0),
                            "beats": item["beats"]})

    if not assignments:
        return {"ok": False, "reason": f"no tagged shot matches the brief: {[p['role'] for p in parsed]}"}

    # build the plan here, not through recipes.expand: each role brings its own beat
    # count, so both shot bounds and cue positions must come from one cumulative grid
    available = recipes._load_all()
    if recipe not in available:
        return {"ok": False, "reason": f"unknown recipe {recipe}; try {sorted(available)}"}
    sections = {section["id"]: section for section in available[recipe]["sections"]}
    frames_per_beat = fps * 60.0 / bpm
    shots, cues, beats_cursor = [], [], 0.0
    for assignment in assignments:
        section = sections.get(assignment["section"])
        if section is None:
            return {"ok": False, "reason": f'recipe {recipe} has no section {assignment["section"]}'}
        start_beat = beats_cursor
        beats_cursor += assignment["beats"]
        start = int(round(start_beat * frames_per_beat))
        end = int(round(beats_cursor * frames_per_beat))
        shots.append({"clip": assignment["clip"], "src_in": assignment["src_in"],
                      "frames": end - start, "slot": assignment["section"]})
        for cue in section.get("sfx", []):
            cues.append({"frame": int(round((start_beat + float(cue["beat"])) * frames_per_beat)),
                         "tag": cue["tag"], "dur_s": cue.get("dur_s")})

    plan = {"timeline": timeline, "fps": fps, "beat_grid": {"bpm": bpm},
            "shots": shots, "sfx": cues, "camera_bed": camera_bed, "music": music}

    normalized, warnings = planner.normalize_plan(plan)
    result = planner.execute_plan(normalized, dry_run=dry_run)
    result["brief"] = [{"role": a["role"], "clip": row["clip"], "src_in": row["src_in"],
                        "frames": row["frames"]} for a, row in zip(assignments, normalized["shots"])]
    if missing:
        result["unmatched_roles"] = missing
    if warnings:
        result["warnings"] = warnings
    return result
