@echo off
REM Cocktail Finder - double-click to start. First run installs dependencies (a few minutes).
setlocal
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
%PY% --version >nul 2>nul || (
  echo Python 3.10 or newer is required. Install it from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" during installation, then run this file again.
  pause & exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo Setting up for first use - this takes a few minutes...
  %PY% -m venv .venv || (pause & exit /b 1)
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r backend\requirements.txt || (echo Installation failed. & pause & exit /b 1)
)
if not defined CF_TESSERACT_CMD if exist "C:\Program Files\Tesseract-OCR\tesseract.exe" set CF_TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
where tesseract >nul 2>nul || if not defined CF_TESSERACT_CMD (
  echo.
  echo WARNING: Tesseract OCR not found. The app will run, but every image without a clear match
  echo will be sent to manual review. Install it from https://github.com/UB-Mannheim/tesseract/wiki
  echo.
)
set CF_DATA_DIR=%~dp0data
set CF_WORKERS=2
echo Starting Cocktail Finder at http://127.0.0.1:8000  (close this window to stop)
start "" "http://127.0.0.1:8000"
cd backend
"..\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause
