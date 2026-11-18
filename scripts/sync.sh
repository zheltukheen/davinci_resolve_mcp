#!/usr/bin/env bash
# Mirror the working copies into this repo (sources only, no venvs or media).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC_ROOT="${1:-/Users/zheltukheen/Documents/адб}"

rsync -a --delete \
  --exclude '.venv' --exclude '__pycache__' --exclude '*.pyc' --exclude '.DS_Store' \
  "$SRC_ROOT/resolve-mcp/" "$ROOT/packages/resolve-mcp/"

rsync -a --delete \
  --exclude '.venv' --exclude '__pycache__' --exclude '*.pyc' --exclude '.DS_Store' \
  "$SRC_ROOT/percept-mcp/" "$ROOT/packages/percept-mcp/"

echo "synced from $SRC_ROOT"
