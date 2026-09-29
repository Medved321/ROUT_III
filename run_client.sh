#!/usr/bin/env sh
# ROUT III client launcher (Linux/Unix).
#
# Finds a suitable CPython 3.12 or provisions one into the user directory
# (tools/ensure_python.sh), then runs the game through the shared bootstrap
# (.venv + requirements.txt) exactly like run_client.bat does on Windows.
#
# Usage: ./run_client.sh [--host H] [--port P] [--name N]
set -e

ROUT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ROUT_ROOT"
export PYTHONUTF8=1

. "$ROUT_ROOT/tools/ensure_python.sh"

ROUT_PY=$(rout_ensure_python)
exec "$ROUT_PY" "$ROUT_ROOT/tools/bootstrap.py" run "$ROUT_ROOT/tools/launch_client.py" "$@"
