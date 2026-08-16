---
name: crm-deployment
description: Access the Beacon AKS cluster and deploy Beacon GTM CRM to staging or production via Helm, with drift checks and strict production safety rules.
---

# Beacon GTM CRM Deployment

Use when the user asks to deploy, push to staging, push to prod, roll out an
image, verify a rollout, or inspect cluster state.

## Hard Rules

- Never deploy unless the user explicitly asks for a deployment in this session.
- Default target is **staging**. Production requires the user to say
  `production` or `prod` explicitly. Approval for staging is never approval for prod.
- Never print, echo, paste, or commit secrets: kubeconfigs, the ACR password,
  bearer tokens, `DATABASE_URL` values, or `.env` contents. Filter command output
  that could contain them (`helm get values`, `kubectl get secret`, the
  `gtm*.yaml` values files — all contain live credentials in plaintext).
- Read-only `kubectl get/describe/logs` is allowed without extra confirmation.
  Helm upgrades, rollouts, restarts, pod deletes, and image changes require
  explicit instruction.
- **Always run the drift gate (below) before a production `helm upgrade`.** The
  chart copy on disk is known to have drifted from what produced the live release.
- Never mix `helm upgrade` and `kubectl set image` on the same release. Mixing
  makes the next Helm upgrade silently revert the image.

## One-Time Machine Setup

Already done on this Mac (verify, don't redo blindly):

| Item | Location / value |
|---|---|
| Kubeconfig | `/Users/sarthak/gtm-secrets/beacon-test-kubeconfig.yaml` (mode `600`) |
| Kube context | `beacon-test` — token auth (no client cert) |
| Helm | `/opt/homebrew/bin/helm` |
| kubectl | `/usr/local/bin/kubectl` |
| buildx builder | `builder` (docker-container driver) |
| Chart (in git) | `deploy/gtm-chart/` — see `deploy/README.md` |
| Env values (NOT in git — hold live secrets) | `~/Downloads/gtm-helm/gtm.yaml`, `gtm-prod.yaml` |

Verify in one shot:

```bash
export KUBECONFIG=/Users/sarthak/gtm-secrets/beacon-test-kubeconfig.yaml
kubectl config current-context && kubectl get ns && helm version --short && docker buildx inspect builder >/dev/null && echo "builder ok"
```

If the buildx builder is missing:

```bash
docker buildx create --name builder --driver docker-container --bootstrap
```

### ACR credential (user runs this, not Codex)

The registry password must be stored in the macOS Keychain by the user. Codex
must never write it — it would pass through the transcript and the process table.

```bash
security add-generic-password -a codebuild -s beacon-acr -U -w
```

Enter the password at the prompt (omitting a value after `-w` makes `security`
prompt instead of taking it from the command line).

## Environment Map

| | Staging | Production |
|---|---|---|
| Namespace | `gtm` | `gtm-prod` |
| URL | https://gtm.staging2.beacon.li | https://gtm.beacon.li |
| Helm release | `gtm` | `gtm` |
| Values file | `~/Downloads/gtm-helm/gtm.yaml` | `~/Downloads/gtm-helm/gtm-prod.yaml` |
| `ENVIRONMENT` | `development` | `production` |

Images: `beacon.azurecr.io/gtm-be:<TAG>` and `beacon.azurecr.io/gtm-fe:<TAG>`.

Routing is Ambassador `Mapping` annotations on the backend Service: `/api/` →
`gtm-backend`, `/` → `gtm-frontend`, same host. This is why the frontend is built
with an **empty** `VITE_API_URL` — it uses relative URLs, so one frontend image
works in both environments.

## Known Drift — verify before trusting anything below

State observed 2026-08-15. Re-check rather than assuming it still holds.

1. **The two environments run different charts.** Prod runs chart `gtm-0.1.0`
   (the `~/Downloads` chart) with deployments named `gtm-*-deployment`. Staging
   was deployed from the **repo-local** `helm/beacon-crm` chart (`beacon-crm-0.1.0`),
   giving deployments named `gtm-beacon-crm-*`. **Never assume deployment names —
   discover them.**
2. **Staging was broken and is now fixed (revisions 186–188, 2026-08-15).**
   Three separate faults, each masking the next — kept here because the traps
   are permanent even though the outage is not:
   - *Read-only filesystem*: `zippy_docs/base.py` calls `ZIPPY_OUTPUT_DIR.mkdir()`
     at import time (`OSError: [Errno 30] ... '/app/storage'`). Fixed by the
     `app-storage` emptyDir in `backend-deployment.yaml`. Backend only —
     `zippy_docs` is imported by `app/main.py` and `zippy_tools.py`, not by any
     module in the Celery `include` list, so workers need no such mount.
   - *ImagePullBackOff ×3*: the chart defaults **every** workload to
     `beacon-crm/backend:latest`, which does not exist in ACR. A deploy that
     overrides only backend and frontend strands worker, beat, and
     priority-worker. Always override all five.
   - *Startup refusal*: the chart's `backend.env.ENVIRONMENT` defaults to
     `production`, which trips `validate_runtime_secrets()` against the chart's
     21-char `jwtSecret` (needs ≥32). `values-staging.yaml` sets `development`.
     This also matters beyond startup: with `ENVIRONMENT=production`,
     `_resolve_report_recipients()` sends scheduled sales reports to the **real**
     recipient list — from staging.
3. **The prod chart had two bugs, both fixed 2026-08-15 and now vendored into
   git at `deploy/gtm-chart/`.** Deploy from the repo copy, not from
   `~/Downloads/gtm-helm/gtm` — that copy is stale and reintroduces both:
   - `templates/backend/worker.yaml` was missing the second Deployment,
     `gtm-priority-worker-deployment`. Helm would have **pruned** the only
     consumer of the `priority` queue (sourcing upload, ICP research, call
     transcription). Restored from the live release manifest.
   - `templates/frontend/server.yaml` had `containerPort`, both probes, and the
     Service `targetPort` on **80**; every `gtm-fe` image serves **8080**.
     Applying it took prod down with 503s. Service `port` stays 80 (the
     Ambassador Mapping addresses the Service with no port), `targetPort` is 8080.

   Secrets are NOT vendored: the per-environment `gtm.yaml`/`gtm-prod.yaml` stay
   at `~/Downloads/gtm-helm/` (gitignored here), and `postgresql.auth.*` in the
   committed `values.yaml` is placeheld with `REPLACE_AT_DEPLOY`. Always pass the
   real values file with `-f`. See `deploy/README.md`.
4. **`DEPLOYMENT_HANDOFF.md` is stale** (Windows paths, older tags/revisions) and
   the separate PDF-style runbook is wrong about Step 4 — the chart takes full
   image strings `backend.image` / `frontend.image`, not an `image.tag` key.
   Prefer `--set-string` over editing `values.yaml`; editing the shared file makes
   staging and prod share a tag.

## Preflight

```bash
export KUBECONFIG=/Users/sarthak/gtm-secrets/beacon-test-kubeconfig.yaml
NS=gtm                    # staging; NS=gtm-prod only when explicitly asked
git -C /Users/sarthak/GTM-tool status --short
git -C /Users/sarthak/GTM-tool rev-parse --short HEAD
```

Discover the real workload names and current images:

```bash
kubectl -n "$NS" get deploy -o custom-columns='NAME:.metadata.name,READY:.status.readyReplicas,IMAGES:.spec.template.spec.containers[*].image'
helm -n "$NS" list
```

Local validation before building:

```bash
cd /Users/sarthak/GTM-tool && make backend-compile && make frontend-build
```

## Tagging

```bash
TAG="v0.$(date +%y%m%d)-$(git -C /Users/sarthak/GTM-tool rev-parse --short HEAD)"
```

Any scheme works as long as the tag is **new** — reusing a tag makes rollout
movement invisible and defeats `imagePullPolicy: Always`. Backend and frontend
may carry different tags; the chart takes them independently.

## Registry Login

```bash
security find-generic-password -a codebuild -s beacon-acr -w \
  | docker login beacon.azurecr.io -u codebuild --password-stdin
```

Expect `Login Succeeded`. Never echo the password or pass it as an argument.

## Build And Push

AKS nodes are amd64. Build `linux/amd64` only — adding arm64 doubles build time
for an artifact nothing runs.

Backend (from repo root — it uses the root `Dockerfile`):

```bash
cd /Users/sarthak/GTM-tool
docker buildx build --platform linux/amd64 . \
  -t beacon.azurecr.io/gtm-be:$TAG --push --builder builder
```

Frontend (from `frontend/` — it has its own Dockerfile and Vite context):

```bash
cd /Users/sarthak/GTM-tool/frontend
docker buildx build --platform linux/amd64 . \
  -t beacon.azurecr.io/gtm-fe:$TAG --build-arg VITE_API_URL= --push --builder builder
```

Confirm both landed:

```bash
docker buildx imagetools inspect beacon.azurecr.io/gtm-be:$TAG
docker buildx imagetools inspect beacon.azurecr.io/gtm-fe:$TAG
```

## Drift Gate — mandatory before production

Run **both** checks. They catch different failures, and skipping the second one
caused a real production outage on 2026-08-15 (see below).

### 1. Field-level diff — the primary gate

`helm-diff` is installed (`helm plugin list` shows `diff`). It shows exactly
which fields change on existing objects:

```bash
cd /Users/sarthak/GTM-tool
helm diff upgrade gtm ./deploy/gtm-chart -n gtm-prod -f ~/Downloads/gtm-helm/gtm-secrets.yaml -f ~/Downloads/gtm-helm/gtm-prod.yaml \
  --set-string backend.image=beacon.azurecr.io/gtm-be:$TAG \
  --set-string frontend.image=beacon.azurecr.io/gtm-fe:$TAG
```

Empty output means no drift. **Read every `-`/`+` pair before proceeding.** Treat
any change to `targetPort`, `containerPort`, probe ports, `selector`, volumes, or
`command`/`args` as a stop-and-ask, not a detail.

Why this is mandatory: live objects are routinely corrected out-of-band
(`kubectl edit`/`patch`) without the chart being updated. A Helm upgrade silently
reverts those corrections. That is precisely what took `gtm.beacon.li` down —
the chart said the frontend `targetPort` was 80, the live Service had been fixed
to 8080, and the upgrade put 80 back. Every `gtm-fe` image serves on 8080
(`nginx-unprivileged` cannot bind privileged ports), so the Service pointed at a
dead port and Ambassador returned 503 for ~9 minutes.

### 2. Workload-existence check — catches pruning

The diff above shows changed objects; this one catches whole workloads the chart
no longer renders, which Helm **deletes** on upgrade.

```bash
cd /Users/sarthak/GTM-tool
helm template gtm ./deploy/gtm-chart -f ~/Downloads/gtm-helm/gtm-secrets.yaml -f ~/Downloads/gtm-helm/gtm-prod.yaml \
  --set-string backend.image=beacon.azurecr.io/gtm-be:$TAG \
  --set-string frontend.image=beacon.azurecr.io/gtm-fe:$TAG 2>/dev/null \
  | grep -E "^kind:|^  name:" | paste - - | grep -Ei "deployment|statefulset" \
  | awk '{print $NF}' | sort > /tmp/rendered.txt

kubectl -n gtm-prod get deploy,statefulset -o name | sed 's|.*/||' | sort > /tmp/live.txt

echo "=== live but NOT rendered (Helm would prune these) ==="
comm -13 /tmp/rendered.txt /tmp/live.txt
```

Anything listed by that last command will be **deleted** by the upgrade. Stop and
report to the user. Do not proceed on the assumption that Helm will "just leave
it alone" — it prunes resources it previously managed.

### 3. Baseline the target BEFORE deploying

Always record what the environment looks like *before* touching it, so you can
tell whether you broke something or found it broken:

```bash
curl -sS -o /dev/null -w "before: %{http_code}\n" https://gtm.beacon.li/     # prod
curl -sS -o /dev/null -w "before: %{http_code}\n" https://gtm.staging2.beacon.li/
kubectl -n "$NS" get deploy -o custom-columns='NAME:.metadata.name,IMAGE:.spec.template.spec.containers[*].image'
```

Skipping this on 2026-08-15 meant several minutes were lost during an outage
just working out whether the deploy had caused it.

## Deploy

### Staging — repo-local `beacon-crm` chart (what `gtm` actually runs)

Staging runs the **repo** chart, whose image values are `repository` + `tag`
pairs, not the single image strings the external chart uses. All **five**
workloads must be overridden — the chart defaults each to
`beacon-crm/backend:latest`, which does not exist in ACR, so any workload you
forget lands in ImagePullBackOff.

`values-staging.yaml` carries the `ENVIRONMENT=development` override and the
Ambassador Mappings — always pass it with `-f`, or staging loses its routing and
refuses to start.

```bash
export KUBECONFIG=/Users/sarthak/gtm-secrets/beacon-test-kubeconfig.yaml
cd /Users/sarthak/GTM-tool
TAG=v0.15-20260814
helm upgrade gtm ./helm/beacon-crm -n gtm -f ./helm/beacon-crm/values-staging.yaml \
  --set-string backend.image.repository=beacon.azurecr.io/gtm-be       --set-string backend.image.tag=$TAG \
  --set-string frontend.image.repository=beacon.azurecr.io/gtm-fe      --set-string frontend.image.tag=$TAG \
  --set-string worker.image.repository=beacon.azurecr.io/gtm-be        --set-string worker.image.tag=$TAG \
  --set-string priorityWorker.image.repository=beacon.azurecr.io/gtm-be --set-string priorityWorker.image.tag=$TAG \
  --set-string beat.image.repository=beacon.azurecr.io/gtm-be          --set-string beat.image.tag=$TAG
```

Secrets note: this chart renders its Secret from `values.yaml`, and the live
release supplies **no** secret overrides — so the committed defaults *are* the
live values. Confirm before upgrading (compare value lengths, never print
values); if the live Secret ever diverges from the chart, a Helm upgrade will
overwrite it.

### Staging — external `gtm` chart

Only if staging is migrated onto the same chart as prod. See the migration
warning below.

```bash
cd /Users/sarthak/GTM-tool
helm upgrade --install gtm ./deploy/gtm-chart -n gtm --create-namespace \
  -f ~/Downloads/gtm-helm/gtm-secrets.yaml -f ~/Downloads/gtm-helm/gtm.yaml \
  --set-string backend.image=beacon.azurecr.io/gtm-be:$TAG \
  --set-string frontend.image=beacon.azurecr.io/gtm-fe:$TAG \
  --kubeconfig /Users/sarthak/gtm-secrets/beacon-test-kubeconfig.yaml
```

Production — only on explicit instruction, and only after the drift gate:

```bash
cd /Users/sarthak/GTM-tool
helm upgrade --install gtm ./deploy/gtm-chart -n gtm-prod --create-namespace \
  -f ~/Downloads/gtm-helm/gtm-secrets.yaml -f ~/Downloads/gtm-helm/gtm-prod.yaml \
  --set-string backend.image=beacon.azurecr.io/gtm-be:$TAG \
  --set-string frontend.image=beacon.azurecr.io/gtm-fe:$TAG \
  --kubeconfig /Users/sarthak/gtm-secrets/beacon-test-kubeconfig.yaml
```

Note: deploying the `gtm` chart into the `gtm` namespace while staging still runs
the `beacon-crm` chart is a **chart migration**, not a routine deploy — it creates
a new `gtm-postgresql` StatefulSet alongside the existing `gtm-beacon-crm-postgres-0`
and does not migrate data. Raise this with the user rather than doing it silently.

## Verify

```bash
for d in $(kubectl -n "$NS" get deploy -o name); do
  kubectl -n "$NS" rollout status "$d" --timeout=300s
done
kubectl -n "$NS" get pods -o wide
```

Expect: rollouts complete, pods `Running`, new pods at `RESTARTS=0`, and the
intended tag on every workload.

Smoke:

```bash
curl -sS -I https://gtm.staging2.beacon.li/ | head -1     # staging
curl -sS -I https://gtm.beacon.li/ | head -1              # production
```

Expect `200`. Then scan logs for the target namespace only:

```bash
kubectl -n "$NS" logs deploy/<backend-deploy> --since=5m --tail=200 \
  | rg -i "traceback|exception|error|failed" || true
```

Separate known external-token noise (Gmail `invalid_grant`, missing API keys)
from genuine rollout failures.

## Rollback

**`helm rollback` on `gtm-prod` is a trap.** Images have been changed with
`kubectl set image` outside Helm, so the stored manifest does not match reality —
on 2026-08-15 Helm's manifest said `gtm-be:v0.60` while prod actually ran
`v0.13-c6e7baf`. A rollback would have shipped a months-old image.

Roll back **forward** instead: re-run `helm upgrade` pinning the previous known
-good tags, which you recorded during the baseline step.

```bash
kubectl -n "$NS" get deploy -o custom-columns='NAME:.metadata.name,IMAGE:.spec.template.spec.containers[*].image'  # capture BEFORE deploying
cd /Users/sarthak/GTM-tool
helm upgrade gtm ./deploy/gtm-chart -n gtm-prod -f ~/Downloads/gtm-helm/gtm-secrets.yaml -f ~/Downloads/gtm-helm/gtm-prod.yaml \
  --set-string backend.image=beacon.azurecr.io/gtm-be:<PREVIOUS_TAG> \
  --set-string frontend.image=beacon.azurecr.io/gtm-fe:<PREVIOUS_TAG>
```

Use `helm history` for information only, and only trust `helm rollback` once the
stored manifest and live state have been reconciled.

Then re-run the verify block.

## Reporting

State plainly:

- Target environment and namespace
- Backend and frontend tags deployed
- Rollout result and pod health
- Smoke result
- Any drift or pre-existing failures found, separated from deploy outcome
- Explicitly say whether production was touched
