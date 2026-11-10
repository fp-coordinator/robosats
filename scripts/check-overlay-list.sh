#!/usr/bin/env bash
# Fails if Dockerfile.overlay COPY lines differ from the api/ python files patched vs the upstream tag.
set -euo pipefail
base="${1:-v0.8.8-alpha}"
cd "$(git rev-parse --show-toplevel)"
want=$(git diff --name-only "$base" HEAD | grep -E "^api/.*\.py$" | sort || true)
have=$(grep -E "^COPY " Dockerfile.overlay | awk "{print \$2}" | sort || true)
if [ "$want" != "$have" ]; then
  echo "MISMATCH: patched files vs Dockerfile.overlay COPY list (< patched, > copied)"
  diff <(echo "$want") <(echo "$have") || true
  exit 1
fi
echo "overlay list OK ($(echo "$want" | wc -l | tr -d " ") files)"
