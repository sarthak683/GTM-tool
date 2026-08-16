---
name: crm-frontend-ui-review
description: Use when changing or reviewing Beacon GTM frontend UI, mobile usability, drawers, popovers, z-index, table density, or visual polish.
---

# CRM Frontend UI Review

Read `AGENTS.md` first.

Use `http://localhost:8080`.

Check:

- desktop layout
- mobile viewport around 390px
- text overflow and wrapping
- z-index and clipping
- popover/dropdown placement
- drawer/modal width and height
- primary action reachability
- hover/focus states
- loading/empty/error states

After edits:

```bash
make frontend-build
make rebuild-frontend
```

