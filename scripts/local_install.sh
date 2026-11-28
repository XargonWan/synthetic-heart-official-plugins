#!/usr/bin/env bash
# Install your extensions the way a person would, from a LOCAL repository, into a throwaway
# runtime root: it proves the package builds, the catalog parses and the installer accepts it.
#
#   scripts/local_install.sh                  # build, add the local repository, install everything
#   scripts/local_install.sh yourname/hello   # install one
#
# Nothing here touches your running SyntH: the runtime root is .local-runtime/ in this
# repository. To try the extension in a running SyntH, see README.md ("Test it locally").
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CORE="${SYNTH_CORE:-$ROOT/../Synthetic_Heart}"
RUNTIME="$ROOT/.local-runtime"

"$ROOT/scripts/build_release.sh" "$ROOT/dist"

cli() {
  PYTHONPATH="$CORE" SYNTH_EXT_ROOT="$RUNTIME" uv run --project "$CORE" python -m core.extensions.cli "$@"
}

rm -rf "$RUNTIME"
cli repo add local "$ROOT/dist" --kind local --unsigned --trusted

if [ $# -gt 0 ]; then ids=("$@"); else
  mapfile -t ids < <(python3 -c 'import json,sys; [print(p["id"]) for p in json.load(open(sys.argv[1]))["packages"]]' "$ROOT/dist/synth-repo.json")
fi
cli install "${ids[@]}" --grant
cli list
