#!/bin/bash
# Runs Work Shadower on your Mac without Docker.
# Uses Homebrew Postgres, local file storage, and the rule-based skill writer (no AI key needed).
#   ./run-local-mac.sh          first run installs things; later runs just start the server
# Then open http://localhost:8000 and sign in as dharmik@example.com (admin).
set -euo pipefail
cd "$(dirname "$0")"
ROOT=$(pwd)

if ! command -v brew >/dev/null; then
  echo "Homebrew is needed: https://brew.sh  (then re-run this script)"
  exit 1
fi

echo "→ Installing Postgres, Node and Python if missing"
for f in postgresql@16 node python@3.12; do
  brew list "$f" >/dev/null 2>&1 || brew install "$f"
done
PG=$(brew --prefix postgresql@16)/bin
PY=$(brew --prefix python@3.12)/bin/python3.12

echo "→ Starting Postgres"
brew services start postgresql@16 >/dev/null
for _ in $(seq 1 20); do "$PG/pg_isready" -q && break; sleep 0.5; done
"$PG/psql" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='workshadower'" | grep -q 1 \
  || "$PG/createdb" workshadower

echo "→ Python environment"
[ -d server/.venv ] || "$PY" -m venv server/.venv
server/.venv/bin/pip install -q -r server/requirements.txt

if [ ! -f web/dist/index.html ]; then
  echo "→ Building the web app"
  (cd web && npm ci --no-audit --no-fund && npm run build)
fi

export DATABASE_URL="postgresql://localhost:5432/workshadower"
export WEB_DIST_DIR="$ROOT/web/dist"
export RUN_WORKER_IN_API=true
export STORAGE_DRIVER=local
export LOCAL_STORAGE_DIR="$ROOT/server/data/assets"
export AUTH_MODE=dev
export ADMIN_EMAILS=dharmik@example.com
export PUBLIC_BASE_URL=http://localhost:8000
export CORS_ORIGINS=http://localhost:8000
export LLM_PROVIDER=${LLM_PROVIDER:-none}
export SECRET_KEY=${SECRET_KEY:-local-dev-only}

cd server
if [ ! -f data/.seeded ]; then
  echo "→ Adding demo data"
  mkdir -p data
  .venv/bin/python -m app.seed && touch data/.seeded
fi

echo
echo "Work Shadower is running at http://localhost:8000   (Ctrl+C to stop)"
echo "In the Dot app: Settings → server http://localhost:8000"
exec .venv/bin/uvicorn app.main:app --port 8000
