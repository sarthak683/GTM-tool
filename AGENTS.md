# Beacon GTM CRM Agent Instructions

This is the canonical instruction file for AI coding agents working in this repo.
Claude Code, Codex, and OpenCode should all read this before making changes.

## Product

Beacon GTM CRM is a custom go-to-market CRM for Beacon.li. It manages pipeline,
account sourcing, prospects, meetings, sales analytics, tasks, Zippy, enrichment,
notifications, and scheduled reporting.

## Stack

- Backend: Python 3.12, FastAPI, SQLModel, Alembic, Celery, Redis
- Database: PostgreSQL 16
- Frontend: React 18, TypeScript, Vite, Tailwind CSS, shadcn-style components
- Local runtime: Docker Compose
- AI/data services: OpenAI, Ollama/local models where configured, Qdrant

## Repository Map

- `app/`: FastAPI backend
- `app/api/v1/endpoints/`: API routers
- `app/models/`: SQLModel table models
- `app/services/`: business logic
- `app/tasks/`: Celery tasks
- `app/clients/`: external API clients
- `alembic/versions/`: database migrations
- `frontend/src/pages/`: page-level React routes
- `frontend/src/components/`: shared React components
- `frontend/src/lib/api/`: typed API clients
- `scripts/`: repo automation and seed/debug scripts
- `docs/`: operating notes and architecture/debug documentation

## Local URLs

- Frontend: `http://localhost:8080`
- Backend API: `http://localhost:8000`
- API docs: `http://localhost:8000/docs`
- Postgres: `localhost:5432`
- Redis: `localhost:6379`
- Qdrant: `localhost:6333`

Important: the Dockerized frontend is served through nginx on port `8080`.
Do not assume `3000` or `5173` unless explicitly running Vite dev mode.

## Common Commands

- Start everything: `docker compose up -d --build`
- Check services: `docker compose ps`
- Rebuild frontend: `docker compose up -d --build frontend`
- Rebuild backend: `docker compose up -d --build backend`
- Rebuild backend + worker + beat: `docker compose up -d --build backend worker beat`
- Run migrations: `docker compose exec -T backend alembic upgrade head`
- Backend tests: `docker compose exec -T backend pytest`
- Frontend build: `docker compose exec -T frontend nginx -t` only checks nginx; use `cd frontend && npm run build` or rebuild the frontend image for TypeScript/Vite.
- Local health smoke: `scripts/smoke/local-health.sh`
- Frontend build smoke: `scripts/smoke/frontend-build.sh`
- Backend compile smoke: `scripts/smoke/backend-pycompile.sh`

## Docker Gotchas

- Backend code is copied into the image. After backend edits, rebuild backend.
- Frontend code is copied into the image. After frontend edits, rebuild frontend.
- The backend container runs Alembic on startup.
- If models change, create an Alembic migration. Do not rely only on SQLModel metadata.
- Local DB data persists in the `postgres_data` volume.

## Coding Rules

- Use async/await for database and external IO in backend request/task paths.
- Use SQLModel models for database tables.
- Every model/schema change that affects the DB must include an Alembic migration.
- API routes should return explicit response models where practical.
- Flexible metadata belongs in JSONB-style fields only when the shape is genuinely variable.
- External APIs go through `app/clients/`.
- Long-running work belongs in Celery tasks.
- Frontend uses functional React components and TypeScript.
- Reuse existing UI patterns before adding new abstractions.
- Keep changes scoped to the user request.

## Safety Rules

- Never hardcode API keys, tokens, passwords, or kube credentials.
- Never print secrets in logs or command output.
- Never commit `.env`, kubeconfigs, ACR passwords, OAuth tokens, or database dumps.
- Never run destructive git commands such as `git reset --hard` or `git checkout --` unless the user explicitly asks.
- Do not revert user changes unless explicitly asked.
- Production and staging changes require explicit user instruction.
- Production DB inspection must be read-only unless explicitly authorized.
- Kubernetes read-only commands are okay; rollouts, restarts, pod deletes, and image updates require explicit authorization.

## Deployment Context

- Mac deployment handoff: `MAC_DEPLOY_HANDOFF.md`
- General deployment handoff: `DEPLOYMENT_HANDOFF.md`
- Prod kubeconfig path used by the owner: `/Users/sarthak/gtm-secrets/beacon-test-kubeconfig.yaml`
- Helm chart copy may exist outside this repo at `/Users/sarthak/Downloads/gtm-helm`

Do not deploy from memory. Read the relevant handoff file first.

## Frontend QA Checklist

After meaningful UI changes:

- Rebuild frontend Docker.
- Open `http://localhost:8080`.
- Test desktop and mobile widths.
- Check that drawers/modals fit viewport and primary actions are reachable.
- Check hover/focus states do not create layout shift.
- Check z-index for popovers/dropdowns/tooltips.
- Check text truncation and wrapping.
- Check browser console for errors if using a browser automation tool.

## Backend QA Checklist

After backend changes:

- Run `python -m py_compile` or targeted compile smoke.
- Rebuild affected backend services.
- Run migrations if schema changed.
- Hit relevant API endpoints with auth if needed.
- Check backend logs for import/startup errors.

## AI Agent Operating Style

- Inspect existing code before editing.
- Prefer `rg` for search.
- Make precise, minimal edits.
- Explain blockers clearly.
- Validate locally when feasible.
- In final responses, list changed files and verification performed.

