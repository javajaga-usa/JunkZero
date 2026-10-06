@echo off
cd /d "%~dp0"
title JunkZero - Intelligent Storage Cleaner
echo Starting JunkZero Desktop GUI...
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m app.main --mode gui
) else (
    python -m app.main --mode gui
)
pause
