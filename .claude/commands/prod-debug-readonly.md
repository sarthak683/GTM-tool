Debug production in read-only mode.

Rules:
- Do not deploy.
- Do not restart pods.
- Do not delete pods.
- Do not write to the database.
- Do not print secrets.

Steps:
1. Read `MAC_DEPLOY_HANDOFF.md` or `DEPLOYMENT_HANDOFF.md`.
2. Use the kubeconfig path from the handoff or user-provided context.
3. Run read-only `kubectl get`, `describe`, and `logs`.
4. Use SELECT-only database queries if needed.
5. Summarize findings, confidence, and proposed fix.

