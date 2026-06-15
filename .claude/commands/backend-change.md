Handle a backend change safely.

Steps:
1. Inspect the relevant endpoint/model/service/task files.
2. If a model changes, add an Alembic migration.
3. Run `make backend-compile`.
4. Rebuild affected services:
   - API-only: `make rebuild-backend`
   - tasks/scheduler/shared database code: `make rebuild-backend-all`
5. Run or describe the targeted API smoke test.
6. Report changed files and verification.

