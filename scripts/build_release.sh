#!/usr/bin/env bash
# Build the assets of a rolling release: one zip per extension plus the signed catalog.
#
#   SYNTH_CORE=~/gits/Synthetic_Heart scripts/build_release.sh [OUTPUT_DIR]
#
# The extensions' sources are in the SyntH repository (extensions_src/), checked out at
# SYNTH_CORE. The output directory (default: dist/) is exactly what the release carries:
#   official-<name>.zip   one package per extension (its version is in its manifest)
#   synth-repo.json       the catalog: every package with its SHA-256, size and platform
#   synth-repo.json.sig   the catalog's Ed25519 signature
# The signing key is SYNTH_PUBLISHER_KEY_FILE (default ~/.config/synth/publisher.key); a
# build without it is not signed, and SyntH refuses an unsigned catalog from this repository.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CORE="${SYNTH_CORE:?set SYNTH_CORE to a checkout of the SyntH repository}"
OUT="${1:-$ROOT/dist}"
KEY="${SYNTH_PUBLISHER_KEY_FILE:-$HOME/.config/synth/publisher.key}"

# What is published, and what a fresh SyntH installs on its own.
EXTENSIONS=(synth_stage synth_dashboard openai_api fluxer telegram_bot zen telegram radio_host protocol_harmony protocol_fish)
DEFAULTS=(official/synth_stage official/synth_dashboard official/openai_api official/fluxer official/telegram_bot official/zen official/protocol_harmony official/protocol_fish)

if [ ! -f "$CORE/extensions_src/synth_stage/web/index.html" ]; then
  echo "building the Stage client"
  "$CORE/scripts/build_stage_extension.sh"
fi

sources=()
for name in "${EXTENSIONS[@]}"; do
  [ -f "$CORE/extensions_src/$name/synth-extension.toml" ] || { echo "no extension $name in $CORE"; exit 1; }
  sources+=("$CORE/extensions_src/$name")
done

sign=()
if [ -f "$KEY" ]; then
  sign=(--sign "$KEY")
else
  echo "WARNING: no publisher key at $KEY: the catalog will NOT be signed"
fi

# The packer needs only these three libraries, not SyntH's own dependencies. Python 3.12
# is the version SyntH's image runs: packages with native code are tagged with it.
run() {
  PYTHONPATH="$CORE" uv run --no-project --python 3.12 \
    --with cryptography --with packaging --with httpx python "$@"
}

rm -rf "$OUT"
run -m core.extensions.cli release "${sources[@]}" --out "$OUT" --name official \
  "${sign[@]}" --default-install "${DEFAULTS[@]}"

if [ ${#sign[@]} -gt 0 ]; then
  run "$ROOT/scripts/verify_release.py" "$OUT" "$ROOT/public.key"
fi
