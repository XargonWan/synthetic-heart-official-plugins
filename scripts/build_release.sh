#!/usr/bin/env bash
# Build the assets of a release: one zip per extension plus the catalog.
#
#   SYNTH_CORE=~/gits/Synthetic_Heart scripts/build_release.sh [OUTPUT_DIR]
#
# Every folder in extensions/ that has a synth-extension.toml is packed (its libraries
# built into lib/). The output directory (default: dist/) is exactly what a release carries:
#   <publisher>-<name>.zip   one package per extension
#   synth-repo.json          the catalog: every package with its SHA-256, size and platform
#   synth-repo.json.sig      the catalog's Ed25519 signature (only when a key is available)
#
# The signing key is SYNTH_PUBLISHER_KEY_FILE (default ~/.config/synth/publisher.key).
# Without it the catalog is NOT signed, which is right for testing on your own machine:
# add the local repository to SyntH with --unsigned (see README.md, "Test it locally").
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CORE="${SYNTH_CORE:-$ROOT/../Synthetic_Heart}"
[ -d "$CORE/core/extensions" ] || { echo "SYNTH_CORE=$CORE is not a SyntH checkout"; exit 1; }
OUT="${1:-$ROOT/dist}"
KEY="${SYNTH_PUBLISHER_KEY_FILE:-$HOME/.config/synth/publisher.key}"

# What a fresh SyntH installs on its own from your repository. Leave it empty unless you
# publish a catalog people add as their main one: an extension is installed by choice.
DEFAULTS=()

sources=()
for dir in "$ROOT"/extensions/*/; do
  [ -f "$dir/synth-extension.toml" ] && sources+=("${dir%/}")
done
[ ${#sources[@]} -gt 0 ] || { echo "no extension in $ROOT/extensions"; exit 1; }

sign=()
if [ -f "$KEY" ]; then
  sign=(--sign "$KEY")
else
  echo "NOTE: no publisher key at $KEY: the catalog will NOT be signed"
fi

# The packer needs only these libraries, not SyntH's own dependencies. Python 3.12 is the
# version SyntH's image runs: packages with native code are tagged with it.
run() {
  PYTHONPATH="$CORE" uv run --no-project --python 3.12 \
    --with cryptography --with packaging --with httpx python "$@"
}

rm -rf "$OUT"
run -m core.extensions.cli release "${sources[@]}" --out "$OUT" --name "${CATALOG_NAME:-my-extensions}" \
  "${sign[@]}" ${DEFAULTS[@]:+--default-install "${DEFAULTS[@]}"}

if [ ${#sign[@]} -gt 0 ] && [ -f "$ROOT/public.key" ]; then
  run "$ROOT/scripts/verify_release.py" "$OUT" "$ROOT/public.key"
fi
