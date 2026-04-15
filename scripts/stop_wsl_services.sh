#!/usr/bin/env bash
set -euo pipefail

for service in backend frontend llmops; do
  pid_file="/tmp/geoverdict/${service}.pid"
  if [[ -f "$pid_file" ]]; then
    pid="$(cat "$pid_file")"
    if kill -0 "$pid" >/dev/null 2>&1; then
      kill "$pid" || true
    fi
    rm -f "$pid_file"
  fi
done

pkill -f "uvicorn app.main:app" || true
pkill -f "next start --hostname 0.0.0.0 --port 3000" || true
pkill -f "next-server" || true
pkill -f "vite preview --host 0.0.0.0 --port 5173" || true
pkill -f "node.*5173" || true

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
docker compose -f "$ROOT_DIR/docker-compose.yml" stop prometheus grafana >/dev/null 2>&1 || true
