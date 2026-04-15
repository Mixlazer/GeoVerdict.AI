#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

mkdir -p /tmp/geoverdict

bash "$ROOT_DIR/scripts/stop_wsl_services.sh" >/dev/null 2>&1 || true

if command -v docker >/dev/null 2>&1; then
  docker compose -f "$ROOT_DIR/docker-compose.yml" up -d prometheus grafana >/tmp/geoverdict/monitoring.log 2>&1 || true
else
  echo "Warning: docker недоступен в этой WSL-среде, поэтому Grafana/Prometheus могут не подняться." >/tmp/geoverdict/monitoring.log
fi

cd "$ROOT_DIR/backend"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
  . .venv/bin/activate
  pip install --upgrade pip
  pip install -e .
else
  . .venv/bin/activate
fi
mkdir -p data
nohup bash -lc "cd '$ROOT_DIR/backend' && . .venv/bin/activate && exec uvicorn app.main:app --host 0.0.0.0 --port 8000" \
  >/tmp/geoverdict/backend.log 2>&1 < /dev/null &
echo $! >/tmp/geoverdict/backend.pid

cd "$ROOT_DIR/frontend"
npm install >/tmp/geoverdict/frontend-install.log 2>&1
npm run build >/tmp/geoverdict/frontend-build.log 2>&1
nohup bash -lc "cd '$ROOT_DIR/frontend' && exec npm run start -- --hostname 0.0.0.0 --port 3000" \
  >/tmp/geoverdict/frontend.log 2>&1 < /dev/null &
echo $! >/tmp/geoverdict/frontend.pid

cd "$ROOT_DIR/llmops-dashboard"
npm install >/tmp/geoverdict/llmops-install.log 2>&1
npm run build >/tmp/geoverdict/llmops-build.log 2>&1
nohup bash -lc "cd '$ROOT_DIR/llmops-dashboard' && exec npm run preview -- --host 0.0.0.0 --port 5173" \
  >/tmp/geoverdict/llmops.log 2>&1 < /dev/null &
echo $! >/tmp/geoverdict/llmops.pid

echo "Backend:  http://127.0.0.1:8000"
echo "Frontend: http://127.0.0.1:3000"
echo "LLMOps:   http://127.0.0.1:5173"
echo "Grafana:  http://127.0.0.1:3001"
echo "Prometheus: http://127.0.0.1:9090"
