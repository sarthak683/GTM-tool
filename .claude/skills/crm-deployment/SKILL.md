---
name: crm-deployment
description: Deploy Beacon GTM CRM to staging or production by following the repo handoff exactly, with strict production/data safety rules.
---

# Beacon GTM CRM Deployment

Use this skill when the user asks to deploy, push to staging, push to production,
roll out an image, verify a deployment, or debug a deployment rollout.

## Hard Rules

- Do not deploy unless the user explicitly asks for a deployment.
- Default target is staging. Production requires the user to explicitly say
  `production` or `prod`.
- Never deploy from memory. Read `AGENTS.md` and the relevant deployment handoff
  first.
- Never print, paste, summarize, or commit secrets, kubeconfigs, registry
  passwords, OAuth tokens, database URLs with passwords, or `.env` contents.
- Never mutate production data unless the user explicitly authorizes the exact
  data-changing operation.
- Kubernetes read-only checks are allowed. Rollouts, image updates, restarts,
  pod deletes, Helm upgrades, and production deploys require explicit user
  instruction.
- If the worktree is dirty, do not revert user changes. Deploy the current
  working tree only when that is what the user asked for.
- Do not mix Helm and `kubectl set image` in the same deploy. Use the method
  stated by the handoff.

## Required Files

Read these before deploying:

1. `AGENTS.md`
2. `MAC_DEPLOY_HANDOFF.md`
3. Any user-provided handoff file, if mentioned

Common local paths used by the owner:

- Repo: `/Users/sarthak/GTM-tool`
- Kubeconfig: `/Users/sarthak/gtm-secrets/beacon-test-kubeconfig.yaml`
- Helm chart copy: `/Users/sarthak/Downloads/gtm-helm/gtm`
- Staging values: `/Users/sarthak/Downloads/gtm-helm/gtm.yaml`
- Production values: `/Users/sarthak/Downloads/gtm-helm/gtm-prod.yaml`

## Environment Map

- Staging namespace: `gtm`
- Staging URL: `https://gtm.staging2.beacon.li`
- Production namespace: `gtm-prod`
- Production URL: `https://gtm.beacon.li`
- Backend image: `beacon.azurecr.io/gtm-be:<tag>`
- Frontend image: `beacon.azurecr.io/gtm-fe:<tag>`

## Preflight

Run safe checks first:

```bash
git status --short
git rev-parse --abbrev-ref HEAD
git rev-parse --short HEAD
docker buildx inspect builder --bootstrap
kubectl --kubeconfig /Users/sarthak/gtm-secrets/beacon-test-kubeconfig.yaml -n gtm get deploy
```

Before building, run the relevant local validation:

```bash
cd /Users/sarthak/GTM-tool
python3 -m py_compile $(find app -name '*.py' -not -path '*/__pycache__/*')
cd frontend && npm run build
```

If the repository has faster smoke targets, prefer them:

```bash
make backend-compile
make frontend-build
```

## Tagging

Use a clear new tag so rollout movement is visible. Recommended format:

```bash
TAG="v0.xx-$(git rev-parse --short HEAD)"
```

If staging is already on the same commit hash, increment the `v0.xx` prefix
rather than reusing the existing tag.

## Registry Login

Use macOS Keychain. Do not print the password.

```bash
ACR_PASSWORD=$(security find-generic-password -a codebuild -s beacon-acr -w)
printf '%s' "$ACR_PASSWORD" | docker login beacon.azurecr.io -u codebuild --password-stdin
unset ACR_PASSWORD
```

Expected output includes `Login Succeeded`.

## Build And Push

Backend:

```bash
cd /Users/sarthak/GTM-tool
docker buildx build \
  --platform linux/amd64 \
  -t beacon.azurecr.io/gtm-be:$TAG \
  --push \
  --builder builder \
  .
```

Frontend:

```bash
cd /Users/sarthak/GTM-tool/frontend
docker buildx build \
  --platform linux/amd64 \
  -t beacon.azurecr.io/gtm-fe:$TAG \
  --build-arg VITE_API_URL= \
  --push \
  --builder builder \
  .
```

Verify both manifests include `linux/amd64`:

```bash
docker buildx imagetools inspect beacon.azurecr.io/gtm-be:$TAG
docker buildx imagetools inspect beacon.azurecr.io/gtm-fe:$TAG
```

## Deploy With Current Operational Flow

Use this when `MAC_DEPLOY_HANDOFF.md` says Helm is drifted/failed and current
operational flow is direct image update.

Set namespace from the explicit target:

```bash
KCFG=/Users/sarthak/gtm-secrets/beacon-test-kubeconfig.yaml
NS=gtm       # staging
# NS=gtm-prod  # production only if explicitly requested
```

Update staging or production workloads:

```bash
kubectl --kubeconfig "$KCFG" -n "$NS" set image deploy/gtm-backend-deployment \
  copilot=beacon.azurecr.io/gtm-be:$TAG \
  run-migrations=beacon.azurecr.io/gtm-be:$TAG

kubectl --kubeconfig "$KCFG" -n "$NS" set image deploy/gtm-frontend-deployment \
  copilot=beacon.azurecr.io/gtm-fe:$TAG

kubectl --kubeconfig "$KCFG" -n "$NS" set image deploy/gtm-worker-deployment \
  copilot=beacon.azurecr.io/gtm-be:$TAG

kubectl --kubeconfig "$KCFG" -n "$NS" set image deploy/gtm-priority-worker-deployment \
  copilot=beacon.azurecr.io/gtm-be:$TAG

kubectl --kubeconfig "$KCFG" -n "$NS" set image deploy/gtm-beat-deployment \
  copilot=beacon.azurecr.io/gtm-be:$TAG
```

The backend deployment has both the `copilot` container and `run-migrations`
init container. They must use the same backend image tag.

## Rollout Verification

```bash
for d in gtm-backend-deployment gtm-frontend-deployment gtm-worker-deployment gtm-priority-worker-deployment gtm-beat-deployment; do
  kubectl --kubeconfig "$KCFG" -n "$NS" rollout status deploy/$d --timeout=300s
done

kubectl --kubeconfig "$KCFG" -n "$NS" get pods -o wide
kubectl --kubeconfig "$KCFG" -n "$NS" get deploy \
  gtm-backend-deployment gtm-frontend-deployment gtm-worker-deployment gtm-priority-worker-deployment gtm-beat-deployment \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.template.spec.initContainers[*].image}{"\t"}{.spec.template.spec.containers[*].image}{"\n"}{end}'
```

Expected:

- All rollout commands succeed.
- Pods are `Running`.
- New rollout pods have `RESTARTS=0`.
- All five workloads show the intended tag.

## Smoke Tests

Staging:

```bash
curl -sS -I https://gtm.staging2.beacon.li/ | sed -n '1,12p'
curl -sS -i https://gtm.staging2.beacon.li/api/v1/auth/google/login | sed -n '1,16p'
```

Production:

```bash
curl -sS -I https://gtm.beacon.li/ | sed -n '1,12p'
curl -sS -i https://gtm.beacon.li/api/v1/auth/google/login | sed -n '1,16p'
```

Expected:

- `/` returns `200 OK` and `content-type: text/html`.
- `/api/v1/auth/google/login` returns a redirect or auth response, not `5xx`.

## Log Check

For the target namespace only:

```bash
kubectl --kubeconfig "$KCFG" -n "$NS" logs deploy/gtm-backend-deployment --since=5m --tail=200 | rg -i "traceback|exception|error|failed|crash" || true
kubectl --kubeconfig "$KCFG" -n "$NS" logs deploy/gtm-worker-deployment --since=5m --tail=120 | rg -i "traceback|exception|error|failed|crash" || true
kubectl --kubeconfig "$KCFG" -n "$NS" logs deploy/gtm-beat-deployment --since=5m --tail=120 | rg -i "traceback|exception|error|failed|crash" || true
```

Report deployment-related errors. Separate known external-token errors, such
as Gmail `invalid_grant`, from rollout failures.

## Final Response Format

Keep the final concise and include:

- Target environment and namespace
- Image tag
- Rollout result
- Smoke result
- Any warnings or known non-deploy issues
- Explicitly state if production was not touched

Example:

```text
Pushed to staging only. Prod was not touched.

Staging is now on v0.xx-abcdef0 for backend, frontend, worker, priority worker, and beat.
All rollouts completed, pods are running with 0 restarts, and smoke checks passed:
- / returns 200
- Google login returns redirect as expected

Warning: worker logs still show Gmail invalid_grant for one account; that is a revoked token issue, not a deploy failure.
```
