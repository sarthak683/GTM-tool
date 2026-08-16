---
name: crm-prod-debug-readonly
description: Use for Beacon GTM production or staging debugging where only read-only kubectl logs, describes, and SELECT-only inspection are allowed.
---

# CRM Prod Debug Readonly

Read `AGENTS.md` and the relevant handoff file first.

Allowed without explicit extra approval:

- `kubectl get`
- `kubectl describe`
- `kubectl logs`
- SELECT-only database queries

Not allowed unless the user explicitly asks:

- deploys
- Helm upgrades
- rollout restarts
- pod deletes
- database writes
- printing secrets

Report evidence, likely root cause, and safest fix.

