#!/usr/bin/env bash
# Staging deploy of v0.260816-417257c (audit-fix release).
# Images are ALREADY built and pushed to ACR; this only upgrades + verifies.
set -euo pipefail
export KUBECONFIG=/Users/sarthak/gtm-secrets/beacon-test-kubeconfig.yaml
cd "$(dirname "$0")/../.."
TAG=v0.260816-417257c

# Baseline (recorded 2026-08-16 by the audit session): helm rev 191, all
# workloads on v0.17-a261d74, https://gtm.staging2.beacon.li returning 200.

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

# Migration + startup sanity
BACKEND=$(kubectl -n gtm get pods -o name | grep backend | head -1)
kubectl -n gtm logs "$BACKEND" --tail=100 | grep -iE "alembic|error|refusing" | head -10 || true

echo "STAGING DEPLOY DONE — verify the System Health panel shows the new Idle states,"
echo "then run scripts/deploy/prod-diff-v0.260816.sh and READ the diff before prod."
