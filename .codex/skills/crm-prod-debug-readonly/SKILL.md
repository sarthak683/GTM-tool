---
name: crm-prod-debug-readonly
description: Use for Beacon GTM production diagnostics when only read-only operations are allowed.
---

# CRM Prod Debug Readonly

Read `AGENTS.md` and deployment handoff files first.

Allowed:

- `kubectl get`
- `kubectl describe`
- `kubectl logs`
- SELECT-only database queries

Not allowed unless explicitly requested:

- deploy
- restart
- pod delete
- database write
- secret printing

Report evidence and recommended fix. Do not mutate production.

