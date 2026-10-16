#!/bin/bash
# macOS launcher: double-click in Finder (or run ./run.command) to start JunkZero from source.
# It makes the .venv on the first run and installs what requirements.txt lists whenever that
# file changes, so a missing package (such as pyobjc, which the Trash needs) is added.
cd "$(dirname "$0")" || exit 1

if [ ! -x ".venv/bin/python" ]; then
    echo "First run: setting up JunkZero's Python environment..."
    if ! python3 -m venv .venv; then
        echo "Python 3 is needed. Install it from https://www.python.org and try again."
        read -r -p "Press Return to close."
        exit 1
    fi
fi

stamp=".venv/.junkzero-requirements"
wanted="$(shasum requirements.txt | cut -d' ' -f1)"
if [ "$(cat "$stamp" 2>/dev/null)" != "$wanted" ]; then
    echo "Installing what JunkZero needs (only when requirements.txt changes)..."
    if .venv/bin/python -m pip install -q -r requirements.txt; then
        echo "$wanted" > "$stamp"
    else
        echo "Could not install everything; JunkZero will start, but some features may not work."
    fi
fi

echo "Starting JunkZero Desktop GUI..."
exec .venv/bin/python -m app.main --mode gui
