#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "== GeoVerdict Release Cleanup =="
echo "repo: $ROOT_DIR"

paths=(
  "$ROOT_DIR/frontend/node_modules"
  "$ROOT_DIR/frontend/.next"
  "$ROOT_DIR/frontend/out"
  "$ROOT_DIR/llmops-dashboard/node_modules"
  "$ROOT_DIR/llmops-dashboard/dist"
  "$ROOT_DIR/llmops-dashboard/.vite"
  "$ROOT_DIR/backend/.venv"
  "$ROOT_DIR/backend/*.egg-info"
  "$ROOT_DIR/backend/data/*.db"
  "$ROOT_DIR/backend/data/*.db-shm"
  "$ROOT_DIR/backend/data/*.db-wal"
  "$ROOT_DIR/backend/data/runtime-config.json"
)

shopt -s nullglob
for pattern in "${paths[@]}"; do
  matches=($pattern)
  for target in "${matches[@]}"; do
    if [[ -e "$target" ]]; then
      rm -rf "$target"
      echo "removed: $target"
    fi
  done
done
shopt -u nullglob

echo "Cleanup complete."
