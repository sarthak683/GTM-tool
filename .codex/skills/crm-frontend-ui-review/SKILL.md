---
name: crm-frontend-ui-review
description: Use when reviewing or fixing Beacon GTM frontend UI, mobile layout, drawers, popovers, or visual polish.
---

# CRM Frontend UI Review

Read `AGENTS.md` first.

Checklist:

- desktop viewport
- mobile viewport around 390px wide
- z-index and clipping
- dropdown/popover placement
- drawer/modal width and height
- primary actions reachable
- text overflow
- hover and focus states
- console/runtime errors

Use `http://localhost:8080`.

After edits:

```bash
make frontend-build
make rebuild-frontend
```

