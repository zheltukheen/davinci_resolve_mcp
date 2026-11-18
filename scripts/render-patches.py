#!/usr/bin/env python3
"""Render cordis.patch.yml from cordis.patch.template.yml next to it."""
import os
import sys

def render(bundle_dir):
    template = os.path.join(bundle_dir, "cordis.patch.template.yml")
    if not os.path.exists(template):
        return None
    text = open(template, encoding="utf-8").read().replace("${BUNDLE_DIR}", bundle_dir)
    target = os.path.join(bundle_dir, "cordis.patch.yml")
    open(target, "w", encoding="utf-8").write(text)
    return target

def main(dirs):
    for directory in dirs:
        bundle = os.path.abspath(directory)
        target = render(bundle)
        print(("rendered " + target) if target else f"no template in {bundle}")

if __name__ == "__main__":
    main(sys.argv[1:] or ["."])
