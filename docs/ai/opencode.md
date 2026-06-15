# OpenCode Repo Instructions

Read `AGENTS.md` first.

OpenCode should treat this repo as Docker-first:

- Local frontend: `http://localhost:8080`
- Backend: `http://localhost:8000`
- Rebuild frontend after frontend changes.
- Rebuild backend after backend changes.
- Do not deploy or mutate production without explicit user instruction.

Prefer these commands:

- `make ps`
- `make smoke`
- `make frontend-build`
- `make backend-compile`
- `make rebuild-frontend`
- `make rebuild-backend`

When reviewing code, lead with bugs, regressions, and missing tests.

