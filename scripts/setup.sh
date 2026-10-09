#!/bin/sh
# One setup path for a new machine (ADR-0093). No assistant needed.
#
#   scripts/setup.sh            # venv + package with dev tooling + .env + doctor
#   scripts/setup.sh --no-dev   # skip the test/lint tooling (a machine that only runs the factory)
#
# Safe to re-run: it reuses .venv, never overwrites an existing .env, never prints a secret.
set -eu
cd "$(dirname "$0")/.."

extra='.[dev]'
[ "${1:-}" = "--no-dev" ] && extra='.'

# the first Python that is 3.11 or newer
python=''
for candidate in python3.13 python3.12 python3.11 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 &&
        "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
        python="$candidate"
        break
    fi
done
if [ -z "$python" ]; then
    echo "Python 3.11 or newer is required (python.org, 'brew install python', 'apt install python3')." >&2
    exit 1
fi
echo "using $($python --version) from $(command -v "$python")"

if [ ! -x .venv/bin/python ]; then
    "$python" -m venv .venv
fi
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -e "$extra"
echo "installed the package ($extra) into .venv"

if [ ! -f .env ]; then
    cp .env.example .env
    echo "created .env from .env.example — open it and fill in the values you need"
else
    echo ".env already exists, left as it is"
fi

echo
echo "Checking what this machine still lacks (docs/SETUP.md explains each line):"
.venv/bin/python doctor.py || true
echo
echo "Next: choose the model for the text roles (any provider; see docs/SETUP.md section 3):"
echo "  .venv/bin/python configure_llm.py <provider> --model <name> --input-price <n> --output-price <n>"
echo "  (add that provider's key to .env), then  .venv/bin/python check_llm.py"
echo "Then 'python doctor.py clipping' (or posting / generation) until it says ready."
echo "Run entrypoints with .venv/bin/python (or 'source .venv/bin/activate'); they read .env themselves."
