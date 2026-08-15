#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

# Run the backend test suite inside the backend container.
#
# The Docker image deliberately excludes tests/ and scripts/ (.dockerignore), so
# a bare `docker compose exec backend pytest` collects ZERO tests and exits 0 —
# it looks green while verifying nothing. This script copies the current
# tests/ + scripts/ into the running container first, and copies app/ too so the
# suite runs against your working tree, not whatever the image was last built
# from.
#
# NOTE: pytest exits 5 (not 0) when it collects no tests, so with `set -e` an
# accidentally-empty run fails loudly here instead of passing silently.

CONTAINER="$(docker compose ps -q backend)"
if [ -z "$CONTAINER" ]; then
  echo "Backend container is not running. Start it with: docker compose up -d backend" >&2
  exit 1
fi

docker cp app "$CONTAINER":/app/
docker cp tests "$CONTAINER":/app/
docker cp scripts "$CONTAINER":/app/
docker cp pyproject.toml "$CONTAINER":/app/pyproject.toml

docker compose exec -T backend pytest -q "$@"

echo "Backend test suite passed."
