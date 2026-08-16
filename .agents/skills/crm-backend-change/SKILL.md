---
name: crm-backend-change
description: Use when implementing Beacon GTM backend changes involving FastAPI routes, SQLModel models, Alembic migrations, Celery tasks, services, or integrations.
---

# CRM Backend Change

Read `AGENTS.md` first.

Workflow:

1. Inspect existing route, model, service, repository, and client patterns.
2. If a DB model changes, add an Alembic migration.
3. Keep external API logic in `app/clients/`.
4. Keep long-running work in Celery tasks.
5. Run:

```bash
make backend-compile
```

6. Rebuild:

```bash
make rebuild-backend
```

For task/shared code:

```bash
make rebuild-backend-all
```

7. Smoke the changed API.

