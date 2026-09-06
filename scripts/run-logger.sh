#!/usr/bin/env bash
# Start the flight logger. Works without `pip install -e .` by putting src/
# on PYTHONPATH.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONUNBUFFERED=1
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m airspace_intel.logger "$@"
