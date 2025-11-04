@echo off
chcp 65001 > nul
if not exist .venv (
    python -m venv .venv
)
call .venv\Scripts\activate.bat
pip install --upgrade pip
pip install -r requirements.txt
pip install pyinstaller
pyinstaller --onefile --name FileInventoryTool app/main.py
