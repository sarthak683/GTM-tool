# Deployment chart (`gtm`)

This is the chart that actually deploys **staging (`gtm`) and production
(`gtm-prod`)**. It is not the same chart as `helm/beacon-crm/` — see below.

It lived only in `~/Downloads/gtm-helm/` until 2026-08-15, unversioned and
unreviewed. Two bugs in it reached production as a result:

- `templates/backend/worker.yaml` was missing its second Deployment,
  `gtm-priority-worker-deployment`. A `helm upgrade` from that copy would have
  **pruned** the only consumer of the `priority` queue (sourcing upload, ICP
  research, call transcription), leaving those tasks queued forever.
- `templates/frontend/server.yaml` set `containerPort`, both probes, and the
  Service `targetPort` to **80**, but every `gtm-fe` image serves on **8080**
  (`nginx-unprivileged` cannot bind privileged ports). Applying it pointed the
  Service at a dead port and took `gtm.beacon.li` down with 503s.

Both are fixed here. Keeping this chart in git is what stops them returning.

## Which chart is which

| Chart | Deploys | Workload names |
|---|---|---|
| `deploy/gtm-chart/` (this one) | `gtm` + `gtm-prod` via the documented flow | `gtm-*-deployment` |
| `helm/beacon-crm/` | staging only, as currently deployed | `gtm-beacon-crm-*` |

Staging currently runs `helm/beacon-crm`. Production runs this chart. They are
not interchangeable — the resource names differ, so switching an environment
between them is a migration, not an upgrade.

## Secrets are NOT in this directory

Two things are deliberately absent, because they contain live credentials:

- `gtm.yaml` / `gtm-prod.yaml` — the per-environment values files. They carry
  `DATABASE_URL` with an inline password plus ~20 API keys each. They stay at
  `~/Downloads/gtm-helm/` and are gitignored here so they cannot be committed by
  accident.
- `postgresql.auth.password` / `rootPassword` in `values.yaml` are placeheld
  with `REPLACE_AT_DEPLOY`.

**Consequence: do not deploy from this directory alone.** The placeholder
passwords would not match the existing database. Always supply the real values
with `-f`, exactly as the deploy commands below do.

## Deploying

Full procedure, including the mandatory drift gate, is in
`.claude/skills/crm-deployment/SKILL.md`. Read it rather than working from
memory — and never skip `helm diff`, which is what would have caught the port
regression above.

**Two `-f` files, in this order.** The first restores the real
`postgresql.auth.*` over the `REPLACE_AT_DEPLOY` placeholders; the second
applies the environment config. Later `-f` wins, so the order matters.

Omitting the first one rewrites the PostgreSQL secret to the literal string
`REPLACE_AT_DEPLOY` and breaks the database. `helm diff` shows this clearly as
`password: '-------- # (32 bytes)'` → `'++++++++ # (17 bytes)'` — 17 being the
length of the placeholder. That is not a cosmetic diff; stop if you see it.

```bash
export KUBECONFIG=/Users/sarthak/gtm-secrets/beacon-test-kubeconfig.yaml
TAG=<the tag you built and pushed>

# ALWAYS run this first and read every -/+ line.
# Expect ONLY image tag changes. Anything touching password, targetPort,
# containerPort, probes, selector or volumes is a stop-and-ask.
helm diff upgrade gtm ./deploy/gtm-chart -n gtm-prod \
  -f ~/Downloads/gtm-helm/gtm/values.yaml \
  -f ~/Downloads/gtm-helm/gtm-prod.yaml \
  --set-string backend.image=beacon.azurecr.io/gtm-be:$TAG \
  --set-string frontend.image=beacon.azurecr.io/gtm-fe:$TAG

helm upgrade gtm ./deploy/gtm-chart -n gtm-prod \
  -f ~/Downloads/gtm-helm/gtm/values.yaml \
  -f ~/Downloads/gtm-helm/gtm-prod.yaml \
  --set-string backend.image=beacon.azurecr.io/gtm-be:$TAG \
  --set-string frontend.image=beacon.azurecr.io/gtm-fe:$TAG
```

Swap `-n gtm-prod` / `gtm-prod.yaml` for `-n gtm` / `gtm.yaml` to target staging
with this chart — but note staging currently runs the other chart.

## Known drift to reconcile

Production images have been changed with `kubectl set image` outside Helm, so
Helm's stored manifest does not match what runs. **`helm rollback` is unsafe**
until that is reconciled — it would ship a months-old image. Roll back by
running `helm upgrade` forward with the previous known-good tag instead.
