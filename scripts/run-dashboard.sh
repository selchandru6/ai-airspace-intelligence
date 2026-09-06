#!/usr/bin/env bash
# Start the lightweight chart-only dashboard (no Flask, no aircraft db).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONUNBUFFERED=1
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m airspace_intel.dashboard_server "$@"
