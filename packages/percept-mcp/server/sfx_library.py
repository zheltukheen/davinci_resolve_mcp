#!/usr/bin/env python3
"""Semantic SFX tags over the local sound library.

The agent asks for a role ("knife", "sizzle", "loud_impact") and gets a concrete
file plus a sensible gain offset, so no file names ever enter the context.
"""
import csv
import os

import config

LIBRARY, CATALOG = config.sound_paths()

# tag -> (preferred item names, fallback keywords, dB above the ambience bed)
TAGS = {
    "knife":       (["cut_onion", "cut_tomato", "cut_vegetables", "cut_chop_wood"],
                    ["chop", "knife", "cut_"], 12),
    "slice":       (["knife_slice", "cut_vegetables", "cut_tomato"],
                    ["slice", "slicing"], 6),
    "peel":        (["cut_peel_vegetables", "cut_peel_carrot", "cut_peel_potato"],
                    ["peel", "grater"], 7),
    "sizzle":      (["fry_wok_sizzle", "fry_pan", "fry_pan_food"],
                    ["sizzl", "fry"], 4),
    "fry":         (["fry_pan_food", "fry_sausage", "fry_bacon", "fry_chicken"],
                    ["fry", "pan"], 5),
    "ignite":      (["stove_ignite"], ["ignit", "stove", "fire"], 8),
    "boil":        (["boil_pot_water", "boil_soup", "simmer_sauce"],
                    ["boil", "simmer", "kettle"], 4),
    "pour":        (["pour_water_pan", "pour_water_quick", "prep_pour_chocolate"],
                    ["pour", "water"], 6),
    "whisk":       (["prep_whisk", "prep_whisk_fork", "prep_beat_eggs"],
                    ["whisk", "beat"], 5),
    "stir":        (["prep_mixing_bowl", "prep_bowl_fork", "prep_stir"],
                    ["mix", "stir", "bowl"], 5),
    "squish":      (["prep_wet_squelch", "prep_splatter_egg"],
                    ["squelch", "squish", "splat"], 6),
    "sprinkle":    (["cut_crush_shred", "prep_crispbread"],
                    ["crush", "shred", "crisp"], 7),
    "squeeze":     (["prep_squeeze", "cut_peel_vegetables"],
                    ["squeeze", "squeak"], 7),
    "crack":       (["prep_egg_crack", "prep_egg_crack_chef", "prep_crispbread"],
                    ["crack", "egg", "snap"], 8),
    "plate":       (["dishes_ceramic_set_down", "dishes_fork_on_plate", "dishes_mug"],
                    ["ceramic", "plate", "fork_on"], 8),
    "cutlery":     (["dishes_cutlery_clatter", "dishes_cup_spoon", "dishes_wash_short"],
                    ["cutlery", "spoon", "clatter"], 6),
    "glass":       (["dishes_glassware_rattle", "dishes_cup_spoon"],
                    ["glass", "rattle"], 7),
    "door":        (["prep_cupboard", "appliance_fridge_open_close"],
                    ["cupboard", "door", "fridge"], 6),
    "bag":         (["prep_plastic_bag", "prep_rummage_cabinet"],
                    ["bag", "rustle", "rummage"], 6),
    "beep":        (["appliance_microwave_beep", "appliance_microwave_beeps"],
                    ["beep", "microwave"], 6),
    "whoosh":      (["ui_card_slide", "ui_card_place"],
                    ["card_slide", "card_place", "whoosh"], 4),
    "impact":      (["impact_soft_heavy_001", "impact_soft_heavy_002"],
                    ["impact_soft_heavy", "impact"], 12),
    "impact_soft": (["impact_soft_med_001", "impact_wood_med_001"],
                    ["impact_soft", "wood"], 8),
    "bell":        (["impact_bell_heavy"], ["bell"], 8),
    "click":       (["ui_click_001", "ui_click_002"], ["click"], 4),
    "water":       (["water_faucet_01", "water_drip_sink", "pour_water_quick"],
                    ["water", "faucet", "drip"], 4),
    "ambience":    (["ambience_kitchen_sink", "ambience_fridge_drone"],
                    ["ambience"], -6),
    "timer":       (["misc_kitchen_timer"], ["timer", "clock"], 6),
}

_cache = None


def load_catalog():
    global _cache
    if _cache is None:
        rows = []
        with open(CATALOG, encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                path = os.path.join(LIBRARY, row["file"])
                if os.path.exists(path):
                    rows.append({**row, "path": path,
                                 "stem": os.path.splitext(os.path.basename(row["file"]))[0]})
        _cache = rows
    return _cache


def all_tags():
    return {tag: {"examples": TAGS[tag][0][:3], "offset_db": TAGS[tag][2]}
            for tag in sorted(TAGS)}


def _matches(rows, names, keywords):
    by_stem = {row["stem"]: row for row in rows}
    picked = [by_stem[name] for name in names if name in by_stem]  # keep priority order
    if picked:
        return picked
    for row in rows:
        haystack = f'{row["stem"]} {row.get("category", "")} {row.get("description", "")}'.lower()
        if any(keyword.lower() in haystack for keyword in keywords):
            picked.append(row)
    return picked


def resolve(tag, variant=0):
    """Return one concrete library item for a semantic tag."""
    if tag not in TAGS:
        raise ValueError(f"unknown sfx tag: {tag}")
    names, keywords, offset = TAGS[tag]
    rows = load_catalog()
    picked = _matches(rows, names, keywords)
    if not picked:
        raise ValueError(f"no library file matches tag {tag}")
    row = picked[variant % len(picked)]
    return {
        "tag": tag,
        "name": row["stem"],
        "path": row["path"],
        "category": row.get("category"),
        "offset_db": offset,
        "duration_s": float(row.get("duration_s") or 0),
    }


def describe(tag):
    names, _, offset = TAGS[tag]
    return f"{tag}: picks from {', '.join(names[:2])}, default {offset:+d} dB over the bed"


def pick(tag, variant=0, rng=None):
    item = resolve(tag, variant)
    if rng is not None and variant == 0:
        pass
    return item


if __name__ == "__main__":
    for tag in all_tags():
        try:
            item = resolve(tag)
            print(f'{tag:12s} -> {item["name"]:28s} {item["offset_db"]:+d} dB')
        except Exception as error:  # noqa: BLE001
            print(f"{tag:12s} -> ERROR {error}")
