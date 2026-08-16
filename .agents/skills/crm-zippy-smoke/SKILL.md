---
name: crm-zippy-smoke
description: Use when changing or testing Zippy chat, sessions, history, rename, pin, delete, document tools, or local Zippy UI behavior.
---

# CRM Zippy Smoke

Read `AGENTS.md` first.

Use `http://localhost:8080`.

Test:

1. Zippy opens.
2. A message can be sent.
3. Response renders.
4. History opens.
5. Session can be selected.
6. Pin/unpin works.
7. Rename works.
8. Delete confirmation works.
9. Deleted test sessions are cleaned up.

If auth is needed, use:

```bash
scripts/smoke/local-token.sh
```

