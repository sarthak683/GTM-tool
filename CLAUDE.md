# Claude Code Instructions

Read `AGENTS.md` first. It is the canonical repo policy.

This file only adds Claude Code-specific workflow preferences.

## Default Workflow

1. Understand the request and inspect the relevant files.
2. Make the smallest complete implementation.
3. Run targeted validation.
4. Rebuild Docker services when required.
5. Report changed files and exact verification.

Do not stop at a plan when the user clearly asked for implementation.

## Local Development

- Frontend URL: `http://localhost:8080`
- Backend URL: `http://localhost:8000`
- Use Docker Compose for the app unless the user explicitly asks for Vite/dev-server mode.
- Rebuild frontend after frontend changes: `docker compose up -d --build frontend`
- Rebuild backend after backend changes: `docker compose up -d --build backend`
- Rebuild backend task services after task changes: `docker compose up -d --build backend worker beat`

## Recommended Claude Commands

Use the command files in `.claude/commands/` for repeated workflows:

- `/local-smoke`
- `/frontend-ui-review`
- `/backend-change`
- `/mobile-qa`
- `/zippy-smoke`
- `/prod-debug-readonly`
- `/staging-deploy`
- `/report-debug`

## Subagents

Use the focused agents in `.claude/agents/` when a task benefits from separation:

- `frontend-ui-reviewer`
- `backend-api-reviewer`
- `qa-smoke-tester`
- `prod-debugger-readonly`
- `deployment-operator`

## Browser Testing

For UI work, prefer an actual browser against `http://localhost:8080`.
Check screenshots/DOM after clicking, opening drawers, hovering popovers, and switching viewport sizes.

## Current Docs

Use Context7 when implementing or debugging framework/library-specific code,
especially React, Vite, Tailwind, FastAPI, SQLAlchemy/SQLModel, Alembic,
Celery, Recharts, Playwright, and MCP setup.

## Production Safety

Production access defaults to read-only.

Allowed without extra confirmation:

- `kubectl get`
- `kubectl describe`
- `kubectl logs`
- read-only database SELECTs

Requires explicit user instruction:

- deploys
- Helm upgrades
- image changes
- pod deletes
- rollout restarts
- database writes
