#!/usr/bin/env bash
# MANDATORY prod drift gate for v0.260816-624f03a. Read EVERY -/+ pair.
# Expected changes: backend/frontend image tags ONLY (no chart edits, no
# migrations, no infra changes in this release). Anything else — especially
# targetPort, probes, selectors, volumes, args — is a STOP-AND-ASK.
set -euo pipefail
export KUBECONFIG=/Users/sarthak/gtm-secrets/beacon-test-kubeconfig.yaml
cd "$(dirname "$0")/../.."
TAG=v0.260816-624f03a

helm diff upgrade gtm ./deploy/gtm-chart -n gtm-prod \
  -f ~/Downloads/gtm-helm/gtm-secrets.yaml -f ~/Downloads/gtm-helm/gtm-prod.yaml \
  --set-string backend.image=beacon.azurecr.io/gtm-be:$TAG \
  --set-string frontend.image=beacon.azurecr.io/gtm-fe:$TAG

echo "=== prune check: live workloads the chart would DELETE (must be empty) ==="
helm template gtm ./deploy/gtm-chart \
  -f ~/Downloads/gtm-helm/gtm-secrets.yaml -f ~/Downloads/gtm-helm/gtm-prod.yaml \
  --set-string backend.image=beacon.azurecr.io/gtm-be:$TAG \
  --set-string frontend.image=beacon.azurecr.io/gtm-fe:$TAG 2>/dev/null \
  | grep -E "^kind:|^  name:" | paste - - | grep -Ei "deployment|statefulset" \
  | awk '{print $NF}' | sort > /tmp/rendered.txt
kubectl -n gtm-prod get deploy,statefulset -o name | sed 's|.*/||' | sort > /tmp/live.txt
comm -13 /tmp/rendered.txt /tmp/live.txt
echo "=== end prune check ==="
