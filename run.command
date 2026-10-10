#!/bin/bash
# macOS launcher: double-click in Finder (or run ./run.command) to start JunkZero from source
cd "$(dirname "$0")"
echo "Starting JunkZero Desktop GUI..."
if [ -x ".venv/bin/python" ]; then
    exec .venv/bin/python -m app.main --mode gui
else
    exec python3 -m app.main --mode gui
fi
