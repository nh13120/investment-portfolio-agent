#!/bin/bash
# One-command setup. Run this once, from inside the portfolio-bot folder:
#
#     bash setup.sh
#
# It creates an isolated Python environment, installs the four libraries this
# project needs, and creates your .env file from the template.

set -e  # stop immediately if any step fails

echo ""
echo "=== Portfolio Bot Setup ==="
echo ""

# --- Check Python exists and is new enough --------------------------------
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python 3 is not installed."
    echo "Install it from https://www.python.org/downloads/ then run this again."
    exit 1
fi

PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "Found Python $PY_VERSION"

PY_OK=$(python3 -c 'import sys; print(1 if sys.version_info >= (3,11) else 0)')
if [ "$PY_OK" != "1" ]; then
    echo "ERROR: Python 3.11 or newer is required (you have $PY_VERSION)."
    echo "Install a newer version from https://www.python.org/downloads/"
    exit 1
fi

# --- Create the virtual environment ---------------------------------------
# A venv is a private copy of Python just for this project, so installing
# libraries here can never break anything else on your Mac.
if [ -d ".venv" ]; then
    echo "Virtual environment already exists, reusing it."
else
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi

# --- Install the libraries -------------------------------------------------
echo "Installing libraries (takes about a minute)..."
./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet -r requirements.txt
echo "Libraries installed."

# --- Create .env from the template ----------------------------------------
if [ -f ".env" ]; then
    echo ".env already exists, leaving it alone."
else
    cp .env.example .env
    echo "Created .env  <-- you must fill in your keys here"
fi

echo ""
echo "=== Setup complete ==="
echo ""
echo "NEXT STEPS:"
echo ""
echo "  1. Open the file called  .env  in VS Code and paste in your 5 keys."
echo "     (README.md Steps 2-4 explain where each key comes from.)"
echo ""
echo "  2. Then run this to check everything works:"
echo ""
echo "       source .venv/bin/activate"
echo "       python scripts/check_setup.py"
echo ""
