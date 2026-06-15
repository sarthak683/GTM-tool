Deploy to staging only after the user explicitly asks.

Steps:
1. Read `MAC_DEPLOY_HANDOFF.md` and any user-provided handoff file.
2. Confirm target namespace and image tag.
3. Build images according to the handoff.
4. Push images without printing credentials.
5. Run Helm upgrade exactly as specified by the handoff.
6. Watch rollout status.
7. Smoke test staging URLs.
8. Report image tags, rollout status, and smoke results.

Do not use this command for production unless the user explicitly says production.

