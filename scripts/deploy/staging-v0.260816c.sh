#!/usr/bin/env bash
# Staging deploy of v0.260816-32833bb (audit fixes + five roadmap features:
# shared metric definitions, workspace-tz windows, soft-delete, company merge
# + alias domains, filter-wide assign). Includes MIGRATION 114 (adds
# deleted_at/additional_domains columns + re-scopes the domain unique index —
# additive, fast, no data rewrite).
# Images are ALREADY built and pushed to ACR; this only upgrades + verifies.
set -euo pipefail
export KUBECONFIG=/Users/sarthak/gtm-secrets/beacon-test-kubeconfig.yaml
cd "$(dirname "$0")/../.."
TAG=v0.260816-32833bb

# Baseline (recorded 2026-08-16): helm rev 192, all workloads on
# v0.260816-417257c, https://gtm.staging2.beacon.li returning 200.
# Rollback = re-run with TAG=v0.260816-417257c.

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

echo "STAGING DEPLOY DONE — expect migration '113 -> 114' in the log above and 200."
