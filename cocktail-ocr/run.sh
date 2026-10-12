#!/usr/bin/env bash
# Build the frontend (if needed) and start the app on http://127.0.0.1:8000
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -f frontend/dist/index.html ]; then
  (cd frontend && npm ci && npm run build)
fi
export CF_DATA_DIR="${CF_DATA_DIR:-$PWD/data}"
cd backend
exec python3 -m uvicorn app.main:app --host "${CF_HOST:-127.0.0.1}" --port "${CF_PORT:-8000}"
