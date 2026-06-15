#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

echo "== Docker services =="
docker compose ps

echo
echo "== Backend health =="
curl -fsS http://localhost:8000/health >/tmp/beacon-health.json
cat /tmp/beacon-health.json
echo

echo
echo "== Frontend =="
curl -fsS -o /dev/null -w "frontend http_status=%{http_code}\n" http://localhost:8080/

echo
echo "== API docs =="
curl -fsS -o /dev/null -w "docs http_status=%{http_code}\n" http://localhost:8000/docs

echo
echo "Local health smoke passed."

