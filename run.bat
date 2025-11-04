@echo off
chcp 65001 > nul
if not exist .venv (
    python -m venv .venv
)
set "PYTHONUTF8=1"
call .venv\Scripts\activate.bat
python -m app.main %*
