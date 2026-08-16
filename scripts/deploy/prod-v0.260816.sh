#!/usr/bin/env bash
# Production deploy of v0.260816-417257c — run ONLY after reading the diff
# from prod-diff-v0.260816.sh. Rolls redis + postgres once (see diff script).
# Roll-forward baseline (recorded 2026-08-16): helm rev 168, everything on
# v0.17-a261d74 — to roll back, re-run this script with TAG=v0.17-a261d74.
set -euo pipefail
export KUBECONFIG=/Users/sarthak/gtm-secrets/beacon-test-kubeconfig.yaml
cd "$(dirname "$0")/../.."
TAG=${TAG:-v0.260816-417257c}

helm upgrade --install gtm ./deploy/gtm-chart -n gtm-prod \
  -f ~/Downloads/gtm-helm/gtm-secrets.yaml -f ~/Downloads/gtm-helm/gtm-prod.yaml \
  --set-string backend.image=beacon.azurecr.io/gtm-be:$TAG \
  --set-string frontend.image=beacon.azurecr.io/gtm-fe:$TAG

for d in $(kubectl -n gtm-prod get deploy -o name); do
  kubectl -n gtm-prod rollout status "$d" --timeout=300s
done
kubectl -n gtm-prod rollout status statefulset/gtm-postgresql --timeout=300s || true
kubectl -n gtm-prod rollout status statefulset/gtm-redis-master --timeout=300s || true
kubectl -n gtm-prod get pods
curl -sS -I https://gtm.beacon.li/ | head -1

# Post-deploy checks: migration 113 landed; postgres recovered cleanly.
kubectl -n gtm-prod exec gtm-postgresql-0 -- bash -c \
  'PGPASSWORD="$(cat $POSTGRES_PASSWORD_FILE)" PGOPTIONS="-c default_transaction_read_only=on" \
   psql -U "$POSTGRES_USER" -d "$POSTGRES_DATABASE" -Atc "SELECT version_num FROM alembic_version"'
kubectl -n gtm-prod logs gtm-postgresql-0 --tail=50 | grep -iE "ready to accept|FATAL|recovery" | tail -3
BACKEND=$(kubectl -n gtm-prod get pods -o name | grep backend | head -1)
kubectl -n gtm-prod logs "$BACKEND" --tail=200 | grep -icE "traceback|error" || true

echo "PROD DEPLOY DONE. Expect: alembic_version=113, 'ready to accept connections',"
echo "error count ~0. Reminder: dashboard call counts will visibly DROP (they were"
echo "3-5x inflated per Aircall call) — tell the team before they see it."
