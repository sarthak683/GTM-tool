---
name: crm-local-smoke
description: Use when validating Beacon GTM locally after code changes.
---

# CRM Local Smoke

Read `AGENTS.md` first.

Run:

```bash
make ps
make smoke
```

If frontend changed:

```bash
make frontend-build
make rebuild-frontend
```

If backend changed:

```bash
make backend-compile
make rebuild-backend
```

If task/shared backend code changed:

```bash
make rebuild-backend-all
```

For UI work, open `http://localhost:8080` with browser tooling and verify the changed workflow.

