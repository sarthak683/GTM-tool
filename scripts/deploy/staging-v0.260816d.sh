#!/usr/bin/env bash
# Staging deploy of v0.260816-ac3e152 (UI/UX overhaul: true-scale typography,
# page layout overhauls, and the AccountSourcingCompanyDetail React #310
# crash fix). NO backend code changes since 32833bb (backend image rebuilt on
# the same source for tag parity). NO new migrations — alembic stays at 114.
# Images are ALREADY built and pushed to ACR; this only upgrades + verifies.
set -euo pipefail
export KUBECONFIG=/Users/sarthak/gtm-secrets/beacon-test-kubeconfig.yaml
cd "$(dirname "$0")/../.."
TAG=v0.260816-ac3e152

# Baseline (recorded 2026-08-16 pre-deploy): staging all workloads on
# v0.260816-32833bb, https://gtm.staging2.beacon.li returning 200.
# Rollback = re-run with TAG=v0.260816-32833bb.

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
kubectl -n gtm logs "$BACKEND" --tail=120 | grep -iE "alembic|error|refusing" | head -10 || true

echo "STAGING DEPLOY DONE — expect 200 and no new migration lines (still 114)."
