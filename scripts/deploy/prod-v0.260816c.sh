#!/usr/bin/env bash
# Production deploy of v0.260816-32833bb — run ONLY after the staging deploy
# verified clean AND the helm diff (image tags ONLY — no chart changes in
# either included commit) has been read. Includes MIGRATION 114 (additive
# columns + partial-unique index swap; ~1.3k companies, sub-second).
# Roll-forward baseline (recorded 2026-08-16): helm rev 169, everything on
# v0.260816-417257c — to roll back, re-run with TAG=v0.260816-417257c.
set -euo pipefail
export KUBECONFIG=/Users/sarthak/gtm-secrets/beacon-test-kubeconfig.yaml
cd "$(dirname "$0")/../.."
TAG=${TAG:-v0.260816-32833bb}

helm diff upgrade gtm ./deploy/gtm-chart -n gtm-prod \
  -f ~/Downloads/gtm-helm/gtm-secrets.yaml -f ~/Downloads/gtm-helm/gtm-prod.yaml \
  --set-string backend.image=beacon.azurecr.io/gtm-be:$TAG \
  --set-string frontend.image=beacon.azurecr.io/gtm-fe:$TAG

echo
read -r -p "Diff above must show IMAGE TAGS ONLY. Proceed with prod upgrade? [yes/NO] " answer
[ "$answer" = "yes" ] || { echo "aborted"; exit 1; }

helm upgrade --install gtm ./deploy/gtm-chart -n gtm-prod \
  -f ~/Downloads/gtm-helm/gtm-secrets.yaml -f ~/Downloads/gtm-helm/gtm-prod.yaml \
  --set-string backend.image=beacon.azurecr.io/gtm-be:$TAG \
  --set-string frontend.image=beacon.azurecr.io/gtm-fe:$TAG

for d in $(kubectl -n gtm-prod get deploy -o name); do
  kubectl -n gtm-prod rollout status "$d" --timeout=300s
done
kubectl -n gtm-prod get pods
curl -sS -I https://gtm.beacon.li/ | head -1

kubectl -n gtm-prod exec gtm-postgresql-0 -- bash -c \
  'PGPASSWORD="$(cat $POSTGRES_PASSWORD_FILE)" PGOPTIONS="-c default_transaction_read_only=on" \
   psql -U "$POSTGRES_USER" -d "$POSTGRES_DATABASE" -Atc "SELECT version_num FROM alembic_version"'
BACKEND=$(kubectl -n gtm-prod get pods -o name | grep backend | head -1)
kubectl -n gtm-prod logs "$BACKEND" --tail=200 | grep -icE "traceback|error" || true

echo "PROD DEPLOY DONE. Expect: alembic_version=114, error count ~0."
echo "NEXT: bash scripts/prod-repair/sourcing-repair-2026-08-16.sh (review, then APPLY=yes)."
