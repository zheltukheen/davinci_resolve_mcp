#!/usr/bin/env python3
"""Path and feature configuration for the perception server.

Nothing here may contain a user name or a checkout path: every default is derived
from the environment or the module location, and every value can be overridden.
"""
import os
import sys

HOME = os.path.expanduser("~")
PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))     # .../percept-mcp/server
BUNDLE_DIR = os.path.dirname(PACKAGE_DIR)                    # .../percept-mcp

# Where Resolve keeps its scripting modules, per platform.
RESOLVE_MODULE_CANDIDATES = {
    "Darwin": [
        "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules",
        os.path.join(HOME, "Library/Application Support/Blackmagic Design/DaVinci Resolve"
                          "/Developer/Scripting/Modules"),
    ],
    "Linux": [
        "/opt/resolve/Developer/Scripting/Modules",
        "/opt/resolve/libs/Fusion/Modules",
    ],
    "Windows": [
        os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"),
                     "Blackmagic Design", "DaVinci Resolve", "Support",
                     "Developer", "Scripting", "Modules"),
    ],
}


def resolve_modules():
    """First existing Resolve scripting module directory, or None."""
    candidates = RESOLVE_MODULE_CANDIDATES.get(sys.platform, []) + \
        RESOLVE_MODULE_CANDIDATES.get("Darwin", [])
    for candidate in candidates:
        if os.path.isdir(candidate):
            return candidate
    return None


def resolve_module_path():
    return resolve_modules() or (RESOLVE_MODULE_CANDIDATES.get(sys.platform) or [None])[0]


CACHE_DIR = os.environ.get("PERCEPT_CACHE", os.path.join(HOME, ".cache", "percept"))

# Sound library: no default that names a person or a project. Point it at a folder
# with a catalog.csv; wav files may sit in the same folder or in a `library/` subfolder.
SOUND_LIBRARY_DIR = os.environ.get("PERCEPT_SFX_LIBRARY", os.path.join(HOME, "SoundLibrary"))
SOUND_CATALOG = os.environ.get("PERCEPT_SFX_CATALOG", os.path.join(SOUND_LIBRARY_DIR, "catalog.csv"))


def sound_paths():
    """(wav tree, catalog) — the tree is `<root>/library` when that exists."""
    root = SOUND_LIBRARY_DIR
    nested = os.path.join(root, "library")
    tree = nested if os.path.isdir(nested) else root
    return tree, SOUND_CATALOG

# Recipes live in the bundle by default and can be pointed elsewhere.
RECIPES_DIR = os.environ.get("PERCEPT_RECIPES", os.path.join(BUNDLE_DIR, "recipes"))

# Optional tools: b-roll ranking and multicam sync. Useful occasionally, not core,
# so they stay out of the default tool list unless explicitly enabled.
EXTRA_TOOLS = os.environ.get("PERCEPT_EXTRA_TOOLS", "").strip().lower() in {"1", "true", "yes", "on"}

MUSIC_DIR = os.environ.get("PERCEPT_MUSIC_DIR")  # optional convenience default


def summary():
    return {
        "package_dir": PACKAGE_DIR,
        "cache": CACHE_DIR,
        "sound_library": SOUND_LIBRARY_DIR,
        "sound_tree": sound_paths()[0],
        "sound_catalog": SOUND_CATALOG,
        "recipes": RECIPES_DIR,
        "resolve_modules": resolve_modules(),
        "extra_tools": EXTRA_TOOLS,
        "music_dir": MUSIC_DIR,
    }


if __name__ == "__main__":
    import json
    print(json.dumps(summary(), indent=1))
