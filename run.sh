#!/bin/bash
# Starts JunkZero from this folder. Works with ./run.sh, bash run.sh, or the file manager's Run.
cd "$(dirname "$0")" || exit 1

# Say what went wrong where the user can see it: in the terminal (kept open until Enter is
# pressed), or in a message box when the file manager started JunkZero without a terminal
fail() {
    echo "$1"
    if [ -t 0 ] && [ -t 1 ]; then
        printf "Press Enter to close..."
        read -r _
    elif command -v zenity >/dev/null 2>&1; then
        zenity --error --title=JunkZero --no-markup --text="$1" 2>/dev/null
    elif command -v notify-send >/dev/null 2>&1; then
        notify-send JunkZero "$1" 2>/dev/null
    fi
    exit 1
}

if ! command -v python3 >/dev/null 2>&1; then
    fail "JunkZero needs Python 3. Install it in a terminal: sudo apt install python3 python3-venv"
fi

if [ ! -x ".venv/bin/python" ]; then
    echo "First run: setting up JunkZero's Python environment..."
    if ! python3 -m venv --system-site-packages .venv; then
        # Debian and MX Linux ship venv without its pip installer (ensurepip) unless python3-venv
        # is installed: make the environment without it and use the system's pip
        rm -rf .venv
        if ! python3 -m venv --without-pip --system-site-packages .venv || ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
            rm -rf .venv
            fail "Python's venv and pip are needed. Install them in a terminal: sudo apt install python3-venv python3-pip"
        fi
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
    elif ! .venv/bin/python -c "import fastapi, uvicorn, send2trash" >/dev/null 2>&1; then
        fail "Could not install what JunkZero needs (is the computer online?). Run ./run.sh again to retry."
    else
        echo "Could not install everything; JunkZero will start, but some features may not work."
    fi
fi

echo "Starting JunkZero..."
exec .venv/bin/python -m app.main --mode gui "$@"
