#!/usr/bin/env bash
# Staging deploy of v0.260816-624f03a (sourcing/prospecting/pipeline audit fixes).
# Images are ALREADY built and pushed to ACR; this only upgrades + verifies.
# No new migrations in this release (schema untouched — code + data-repair only).
set -euo pipefail
export KUBECONFIG=/Users/sarthak/gtm-secrets/beacon-test-kubeconfig.yaml
cd "$(dirname "$0")/../.."
TAG=v0.260816-624f03a

# Baseline (recorded 2026-08-16): helm rev 192, all workloads on
# v0.260816-417257c, https://gtm.staging2.beacon.li returning 200.

helm upgrade gtm ./helm/beacon-crm -n gtm -f ./helm/beacon-crm/values-staging.yaml \
  --set-string backend.image.repository=beacon.azurecr.io/gtm-be        --set-string backend.image.tag=$TAG \
  --set-string frontend.image.repository=beacon.azurecr.io/gtm-fe       --set-string frontend.image.tag=$TAG \
  --set-string worker.image.repository=beacon.azurecr.io/gtm-be         --set-string worker.image.tag=$TAG \
  --set-string priorityWorker.image.repository=beacon.azurecr.io/gtm-be --set-string priorityWorker.image.tag=$TAG \
  --set-string beat.image.repository=beacon.azurecr.io/gtm-be           --set-string beat.image.tag=$TAG

for d in $(kubectl -n gtm get deploy -o name); do
  kubectl -n gtm rollout status "$d" --timeout=300s
done
kubectl -n gtm get pods
curl -sS -I https://gtm.staging2.beacon.li/ | head -1

BACKEND=$(kubectl -n gtm get pods -o name | grep backend | head -1)
kubectl -n gtm logs "$BACKEND" --tail=100 | grep -iE "alembic|error|refusing" | head -10 || true

echo "STAGING DEPLOY DONE — spot-check: Prospecting hides not_a_fit/dnd accounts'"
echo "prospects, the Account-status filter reveals them, Pipeline drag-drop shows"
echo "an error toast on failure. Then run scripts/deploy/prod-diff-v0.260816b.sh"
echo "and READ the diff before prod."
