Deploy to staging only after the user explicitly asks.

Use the `crm-deployment` skill — it is the self-contained source of truth for
cluster access, chart paths, namespaces, build commands, and the drift gate.
(`MAC_DEPLOY_HANDOFF.md` does not exist; `DEPLOYMENT_HANDOFF.md` is stale and
Windows-oriented — treat it as history, not instruction.)

Steps:
1. Load the `crm-deployment` skill and follow it.
2. Run its preflight, including discovering the real deployment names — staging
   and production currently run different charts with different names.
3. Confirm target namespace and image tag with the user.
4. Build and push images without printing credentials.
5. Run the Helm upgrade exactly as the skill specifies.
6. Watch rollout status.
7. Smoke test staging URLs.
8. Report image tags, rollout status, and smoke results.

Do not use this command for production unless the user explicitly says production.
