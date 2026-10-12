#!/usr/bin/env bash
# Cocktail Finder - double-click (Mac) or run ./start-mac-linux.command. First run installs dependencies.
set -e
cd "$(dirname "$0")"
PY=$(command -v python3 || command -v python || true)
if [ -z "$PY" ]; then
  echo "Python 3.10+ is required: https://www.python.org/downloads/"; read -r -p "Press Enter to close"; exit 1
fi
if [ ! -x .venv/bin/python ]; then
  echo "Setting up for first use - this takes a few minutes..."
  "$PY" -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r backend/requirements.txt
fi
if ! command -v "${CF_TESSERACT_CMD:-tesseract}" >/dev/null 2>&1; then
  for p in /opt/homebrew/bin/tesseract /usr/local/bin/tesseract; do [ -x "$p" ] && export CF_TESSERACT_CMD="$p"; done
fi
command -v "${CF_TESSERACT_CMD:-tesseract}" >/dev/null 2>&1 || echo "WARNING: Tesseract not found (Mac: brew install tesseract, Ubuntu: sudo apt install tesseract-ocr). Images without a clear match will all go to manual review."
export CF_DATA_DIR="$PWD/data" CF_WORKERS="${CF_WORKERS:-2}"
echo "Starting Cocktail Finder at http://127.0.0.1:8000  (press Ctrl+C or close this window to stop)"
( sleep 4; (command -v open >/dev/null && open http://127.0.0.1:8000) || (command -v xdg-open >/dev/null && xdg-open http://127.0.0.1:8000) || true ) &
cd backend
exec ../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
