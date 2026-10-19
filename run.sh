#!/bin/bash
# Linux launcher (MX Linux, Debian, Ubuntu...): run ./run.sh to start JunkZero from source.
# It makes the .venv on the first run and installs what requirements.txt lists whenever that
# file changes. The .venv can see the system's own Python packages, so JunkZero opens in its own
# window when python3-gi and gir1.2-webkit2-4.1 are installed, and in the web browser otherwise.
cd "$(dirname "$0")" || exit 1

if [ ! -x ".venv/bin/python" ]; then
    echo "First run: setting up JunkZero's Python environment..."
    if ! python3 -m venv --system-site-packages .venv; then
        rm -rf .venv
        echo "Python 3 with venv is needed. Install it in a terminal: sudo apt install python3-venv"
        exit 1
    fi
fi

stamp=".venv/.junkzero-requirements"
wanted="$(sha256sum requirements.txt | cut -d' ' -f1)"
if [ "$(cat "$stamp" 2>/dev/null)" != "$wanted" ]; then
    echo "Installing what JunkZero needs (only when requirements.txt changes)..."
    if .venv/bin/python -m pip install -q --use-pep517 -r requirements.txt; then
        echo "$wanted" > "$stamp"
    elif grep -v '^pywebview' requirements.txt > .venv/requirements-browser.txt &&
            .venv/bin/python -m pip install -q --use-pep517 -r .venv/requirements-browser.txt; then
        echo "JunkZero's own window could not be installed, so it will open in your web browser."
        echo "$wanted" > "$stamp"
    else
        echo "Could not install everything; JunkZero will start, but some features may not work."
    fi
fi

echo "Starting JunkZero..."
exec .venv/bin/python -m app.main --mode gui "$@"
