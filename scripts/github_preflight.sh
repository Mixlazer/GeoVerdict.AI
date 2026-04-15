#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "== GeoVerdict GitHub Preflight =="
echo "repo: $ROOT_DIR"

echo
echo "[1/4] Checking for files larger than 100 MB..."
large_files="$(find "$ROOT_DIR" \
  \( -path '*/node_modules' -o -path '*/.venv' -o -path '*/dist' -o -path '*/.next' \) -prune \
  -o -type f -size +100M -print 2>/dev/null)"
if [[ -n "$large_files" ]]; then
  echo "Found files that exceed GitHub's 100 MB limit:"
  echo "$large_files"
  exit 1
fi
echo "OK: no tracked-source files exceed 100 MB."

echo
echo "[2/4] Checking for common local runtime artifacts..."
runtime_hits="$(find "$ROOT_DIR" \
  \( -path '*/node_modules' -o -path '*/.venv' -o -path '*/.next' -o -path '*/dist' -o -name '*.db' -o -name 'runtime-config.json' \) \
  -print 2>/dev/null)"
if [[ -n "$runtime_hits" ]]; then
  echo "Warning: local artifacts exist in the workspace. They are expected to stay ignored:"
  echo "$runtime_hits"
else
  echo "OK: no obvious local runtime artifacts found."
fi

echo
echo "[3/4] Checking for obviously filled API key env vars in tracked examples..."
if grep -nE 'API_KEY=.+|SECRET_KEY=.+' "$ROOT_DIR/.env.example" | grep -vE '=$'; then
  echo "Potential secret-like value found in .env.example"
  exit 1
fi
echo "OK: .env.example does not contain filled secrets."

echo
echo "[4/4] Suggested publish commands:"
cat <<'EOF'
git init
git add .
git status
git commit -m "Initial commit"
git branch -M main
git remote add origin <your-repo-url>
git push -u origin main
EOF

echo
echo "Preflight complete."
