#!/usr/bin/env python3
"""JSON edit recipes: the model picks a template and supplies only clips and in-points."""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
RECIPE_DIR = os.environ.get("PERCEPT_RECIPES", os.path.join(os.path.dirname(HERE), "recipes"))


def _load_all():
    recipes = {}
    if not os.path.isdir(RECIPE_DIR):
        return recipes
    for name in sorted(os.listdir(RECIPE_DIR)):
        if not name.endswith(".json"):
            continue
        recipe = json.load(open(os.path.join(RECIPE_DIR, name), encoding="utf-8"))
        recipes[recipe["name"]] = recipe
    return recipes


def list_recipes():
    recipes = _load_all()
    if not recipes:
        return {"ok": False, "reason": f"no recipes in {RECIPE_DIR}"}
    out = {}
    for name, recipe in recipes.items():
        out[name] = {
            "title": recipe.get("title"),
            "beat_grid": {"fps": recipe.get("fps"), "bpm": recipe.get("bpm")},
            "sections": [{"id": section["id"], "beats": section["beats"],
                          "purpose": section.get("purpose"),
                          "sfx": [cue["tag"] for cue in section.get("sfx", [])]}
                         for section in recipe["sections"]],
        }
    return {"ok": True, "recipes": out}


def expand(name, assignments):
    """Turn recipe sections + per-section clip assignments into a plan."""
    recipes = _load_all()
    if name not in recipes:
        raise ValueError(f"unknown recipe {name}; try {sorted(recipes)}")
    recipe = recipes[name]
    fps = float(recipe.get("fps") or 50)
    bpm = float(recipe.get("bpm") or 0)
    sections = {section["id"]: section for section in recipe["sections"]}
    frames_per_beat = fps * 60.0 / bpm if bpm else 0.0

    shots, cues = [], []
    beats_cursor = 0.0
    for assignment in assignments:
        section_id = assignment.get("section")
        if section_id not in sections:
            raise ValueError(f"section {section_id} is not in recipe {name}")
        section = sections[section_id]
        start_beat = beats_cursor
        beats_cursor += float(section["beats"])
        start = int(round(start_beat * frames_per_beat))
        end = int(round(beats_cursor * frames_per_beat))
        shots.append({"clip": assignment["clip"], "src_in": int(assignment.get("src_in") or 0),
                      "frames": end - start, "slot": section_id})
        for cue in section.get("sfx", []):
            beat = start_beat + float(cue["beat"])
            cues.append({"frame": int(round(beat * frames_per_beat)),
                         "tag": cue["tag"], "dur_s": cue.get("dur_s")})

    return {
        "timeline": None,
        "fps": fps,
        "beat_grid": {"bpm": bpm},
        "shots": shots,
        "sfx": cues,
    }


if __name__ == "__main__":
    print(json.dumps(list_recipes(), ensure_ascii=False, indent=1)[:600])
