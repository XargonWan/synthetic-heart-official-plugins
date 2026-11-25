#!/usr/bin/env bash
# Rebuild this repository (packages, signed index) from the SyntH sources.
#
#   SYNTH_CORE=~/gits/Synthetic_Heart ./publish.sh
#
# The extensions' sources live in the SyntH repository (extensions_src/) for now; this
# packs each one with its libraries, signs the index with the publisher key (which never
# goes in a repository) and lists packages too big for Git (over 90 MB) by URL, as release
# assets: upload what lands in dist/ to the release named by RELEASE_TAG.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE="${SYNTH_CORE:-$HOME/gits/Synthetic_Heart}"
KEY="${SYNTH_PUBLISHER_KEY:-$HOME/.config/synth/publisher.key}"
RELEASE_TAG="${RELEASE_TAG:-v0.1.0}"
RELEASES="${RELEASES_URL:-https://github.com/XargonWan/synthetic-heart-official-plugins/releases/download}"
EXTENSIONS=(synth_stage openai_api fluxer telegram_bot zen telegram)
# Installed on their own at a fresh SyntH's first start.
DEFAULTS=(official/synth_stage official/openai_api official/fluxer official/telegram_bot official/zen)

[ -f "$KEY" ] || { echo "no publisher key at $KEY (python -m core.extensions.cli keygen)"; exit 1; }
[ -f "$CORE/extensions_src/synth_stage/web/index.html" ] || {
  echo "the Stage client is not built: run $CORE/scripts/build_stage_extension.sh first"; exit 1; }

rm -rf "$ROOT/packages" "$ROOT/dist"
cd "$CORE"
for name in "${EXTENSIONS[@]}"; do
  uv run python -m core.extensions.cli pack "extensions_src/$name" --out "$ROOT"
done

# A Git host refuses files over 100 MB: keep big packages out of the tree (dist/).
mkdir -p "$ROOT/dist"
find "$ROOT/packages" -name '*.synthx' -size +90M -exec mv {} "$ROOT/dist/" \;
find "$ROOT/packages" -type d -empty -delete

uv run python -m core.extensions.cli index "$ROOT" --name official --sign "$KEY" \
  --default-install "${DEFAULTS[@]}" \
  --url-base "$RELEASES/$RELEASE_TAG/" --url-above-mb 90
echo
echo "Big packages to upload as assets of release $RELEASE_TAG:"
ls -la "$ROOT/dist" 2>/dev/null || true
