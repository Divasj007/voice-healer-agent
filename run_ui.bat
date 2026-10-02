@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [Voice Healer] Virtual environment not found.
  echo Create it with: python -m venv .venv
  exit /b 1
)
".venv\Scripts\python.exe" "skills\voice-healer\scripts\ui_server.py" --open
endlocal
