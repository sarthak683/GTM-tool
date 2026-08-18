#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

# Two passes, because they catch different things and the first alone gave
# false confidence.
#
# 1. compileall — syntax only. It happily compiles a file that calls an
#    undefined name, imports a module twice, or shadows an import with a local.
# 2. ruff — the real gate (rules and rationale in pyproject.toml). Same command
#    CI runs, so a failure surfaces here rather than after a push. Against 8
#    seeded regressions taken from real bugs in this repo, compileall caught 2
#    and ruff caught 8.
#
# IMPORTANT: ruff must run against the WORKING TREE. An earlier version of this
# script did `docker compose exec backend ruff check app`, which lints the copy
# baked into the image — so local edits were never checked and the gate passed
# on a file that had a duplicate import. Mount the tree instead of exec'ing.

python3 -m compileall -q app scripts

RUFF_IMAGE="${RUFF_IMAGE:-gtm-tool-backend:latest}"

if command -v ruff >/dev/null 2>&1; then
  ruff check --no-cache app scripts
elif docker image inspect "$RUFF_IMAGE" >/dev/null 2>&1; then
  # ruff is pinned in requirements.txt so the backend image always has it, even
  # when the host does not. Read-only mount of the working tree.
  docker run --rm -v "$PWD:/src:ro" -w /src "$RUFF_IMAGE" ruff check --no-cache app scripts
else
  echo "ERROR: no ruff on host and image '$RUFF_IMAGE' not found." >&2
  echo "       Build it with: docker compose build backend" >&2
  echo "       Syntax was checked, but the lint gate did NOT run." >&2
  exit 1
fi

echo "Backend compile + lint gate passed."
