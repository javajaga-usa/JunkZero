@echo off
rem Windows launcher: double-click to start JunkZero from source. It makes the .venv on the first
rem run and installs what requirements.txt lists whenever that file changes, like run.command.
cd /d "%~dp0"
title JunkZero - Intelligent Storage Cleaner

if not exist ".venv\Scripts\python.exe" (
    echo First run: setting up JunkZero's Python environment...
    python -m venv .venv >nul 2>&1 || py -3 -m venv .venv
    if not exist ".venv\Scripts\python.exe" (
        echo.
        echo Python 3.10 or later is needed. Install it from https://www.python.org/downloads/
        echo ^(tick "Add python.exe to PATH"^), or download the ready-made JunkZero from
        echo https://github.com/javajaga-usa/JunkZero/releases/latest instead.
        pause
        exit /b 1
    )
)

rem The copy of requirements.txt kept in .venv says what was installed last time
fc /b requirements.txt .venv\.junkzero-requirements.txt >nul 2>&1
if errorlevel 1 (
    echo Installing what JunkZero needs ^(only when requirements.txt changes^)...
    ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
    if errorlevel 1 (
        echo Could not install everything; JunkZero will start, but some features may not work.
    ) else (
        copy /y requirements.txt .venv\.junkzero-requirements.txt >nul
    )
)

echo Starting JunkZero...
".venv\Scripts\python.exe" -m app.main --mode gui %*
rem Keep the window open only when JunkZero stopped with an error, so it can be read
if errorlevel 1 pause
