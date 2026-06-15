---
name: crm-local-smoke
description: Use when validating Beacon GTM locally after code changes, Docker rebuilds, or before reporting that localhost is ready.
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

If Celery tasks, scheduler, shared DB code, or report code changed:

```bash
make rebuild-backend-all
```

For UI work, test `http://localhost:8080` in a browser.

