@echo off
REM Run the PiHome hub on Windows in simulation mode (no Raspberry Pi needed)
cd /d "%~dp0..\hub"
if not exist .venv (
  echo Creating virtual environment...
  python -m venv .venv
  .venv\Scripts\pip install -r requirements.txt
)
echo Open http://localhost:8000 in your browser
.venv\Scripts\python main.py --mock
