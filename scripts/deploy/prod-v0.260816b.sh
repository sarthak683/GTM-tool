#!/usr/bin/env bash
# Production deploy of v0.260816-624f03a — run ONLY after reading the diff
# from prod-diff-v0.260816b.sh (expect image tags only; no migrations, no
# redis/postgres rolls this time).
# Roll-forward baseline (recorded 2026-08-16): helm rev 169, everything on
# v0.260816-417257c — to roll back, re-run this script with TAG=v0.260816-417257c.
set -euo pipefail
export KUBECONFIG=/Users/sarthak/gtm-secrets/beacon-test-kubeconfig.yaml
cd "$(dirname "$0")/../.."
TAG=${TAG:-v0.260816-624f03a}

helm upgrade --install gtm ./deploy/gtm-chart -n gtm-prod \
  -f ~/Downloads/gtm-helm/gtm-secrets.yaml -f ~/Downloads/gtm-helm/gtm-prod.yaml \
  --set-string backend.image=beacon.azurecr.io/gtm-be:$TAG \
  --set-string frontend.image=beacon.azurecr.io/gtm-fe:$TAG

for d in $(kubectl -n gtm-prod get deploy -o name); do
  kubectl -n gtm-prod rollout status "$d" --timeout=300s
done
kubectl -n gtm-prod get pods
curl -sS -I https://gtm.beacon.li/ | head -1

# Post-deploy checks: still on migration 113 (none added); backend clean.
kubectl -n gtm-prod exec gtm-postgresql-0 -- bash -c \
  'PGPASSWORD="$(cat $POSTGRES_PASSWORD_FILE)" PGOPTIONS="-c default_transaction_read_only=on" \
   psql -U "$POSTGRES_USER" -d "$POSTGRES_DATABASE" -Atc "SELECT version_num FROM alembic_version"'
BACKEND=$(kubectl -n gtm-prod get pods -o name | grep backend | head -1)
kubectl -n gtm-prod logs "$BACKEND" --tail=200 | grep -icE "traceback|error" || true

echo "PROD DEPLOY DONE. Expect: alembic_version=113, error count ~0."
echo "NEXT: run the one-time data repair —"
echo "  bash scripts/prod-repair/sourcing-repair-2026-08-16.sh          # review"
echo "  APPLY=yes bash scripts/prod-repair/sourcing-repair-2026-08-16.sh # repair"
echo "Heads-up for the team: prospects of not_a_fit/dnd accounts disappear from"
echo "Prospecting by default (use the new Account-status filter to review them)."
