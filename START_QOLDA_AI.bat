@echo off
setlocal
cd /d "%~dp0"
if not exist ".env" (
  echo ERROR: Rename .env.example to .env and paste OPENAI_API_KEY.
  pause
  exit /b 1
)
python -m pip install -r requirements.txt
if errorlevel 1 pause & exit /b 1
python qolda_ai_server.py
pause
