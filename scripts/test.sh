#!/usr/bin/env bash
# Run this repository's tests in the SyntH checkout's environment.
#
#   SYNTH_CORE=~/gits/Synthetic_Heart scripts/test.sh [pytest arguments]
#
# SYNTH_CORE defaults to ../Synthetic_Heart. The tests use the Core's gateway and helpers,
# so they run with the Core's Python environment (uv sync there once).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export SYNTH_CORE="${SYNTH_CORE:-$ROOT/../Synthetic_Heart}"
[ -d "$SYNTH_CORE/core/extensions" ] || { echo "SYNTH_CORE=$SYNTH_CORE is not a SyntH checkout"; exit 1; }

cd "$SYNTH_CORE"
DB_HOST=127.0.0.1 DB_PORT=1 uv run pytest -c "$ROOT/pytest.ini" --rootdir "$ROOT" "$ROOT/ext_tests" "$@"
