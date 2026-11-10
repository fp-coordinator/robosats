#!/usr/bin/env bash
# Fails if Dockerfile.overlay COPY lines differ from the api/ Python files patched
# vs the upstream base commit.
#
# Tests (api/tests/) and migrations (api/migrations/) are excluded from the COPY
# requirement: migrations are baked into the upstream image, and the overlay must
# never add new migration files. Any migration file detected in the patch is a
# hard failure -- migrations added by a patch would not be applied on upstream
# containers that do not include the COPY.
#
# Usage: bash scripts/check-overlay-list.sh [base-sha-or-tag]
#   base defaults to the newest v*-alpha tag reachable from HEAD.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

base="${1:-}"
if [ -z "$base" ]; then
    base=$(git tag --sort=-version:refname | grep -E '^v[0-9].*-alpha$' | head -1 || true)
    if [ -z "$base" ]; then
        echo "ERROR: no v*-alpha tag found and no base argument given" >&2
        exit 1
    fi
    echo "Using base: $base"
fi

all_py=$(git diff --name-only "$base" HEAD | grep -E "^api/.*\.py$" | sort || true)

migration_files=$(echo "$all_py" | grep "^api/migrations/" || true)
if [ -n "$migration_files" ]; then
    echo "ERROR: overlay adds migration files -- migrations must come from the upstream image only" >&2
    echo "$migration_files" >&2
    exit 1
fi

want=$(echo "$all_py" | grep -Ev "^api/(tests|migrations)/" | sort || true)
have=$(grep -E "^COPY " Dockerfile.overlay | awk '{print $2}' | sort || true)

if [ "$want" != "$have" ]; then
    echo "MISMATCH: patched files vs Dockerfile.overlay COPY list (< expected, > actual)"
    diff <(echo "$want") <(echo "$have") || true
    exit 1
fi

echo "overlay list OK ($(echo "$want" | wc -l | tr -d ' ') files)"
