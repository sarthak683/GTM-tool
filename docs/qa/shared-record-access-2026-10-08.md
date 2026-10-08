# Shared account and prospect access

Request: all authenticated teammates can view accounts and prospects. Only a record's assigned AE/SDR or an admin may edit it. AEs and SDRs may delete prospects, including prospects assigned to other reps. Account deletion remains admin-only.

## Implementation

- Shared account/prospect read scope across lists, detail, search, summaries and selectors; soft-deleted accounts remain excluded. Optional owner/status filters still work.
- Separate, side-effect-free ownership checks for editing. Viewing does not auto-claim unassigned records or grant editing through a linked deal.
- Guard account updates, notes and research; prospect updates and outreach; single/bulk assignment; event tags; CSV overwrite paths; angel mappings and cadence enrollment.
- Editing and assignment controls become read-only on records owned by others. SDRs can select the shared prospect view. Account activity remains readable.
- Correct account-detail mobile grid specificity so actions do not overlap account information.

## Validation

- Full backend suite: 729 passed, 2 opt-in database tests skipped, 19 subtests passed.
- Opt-in PostgreSQL deletion suite subsequently enabled: 9 passed, including the 2 cadence dependency tests.
- Frontend ownership/filter tests: 8 passed.
- TypeScript/Vite build and backend/frontend Docker builds passed.
- Isolated local PostgreSQL upgraded through migration 144; no migration required by this change.
- Live HTTP checks for admin, superadmin, AE, SDR and marketing: foreign-record reads allowed, foreign edits denied except admins, account deletion denied for non-admins. Reassignment/event-tag bypasses denied. Assigned AE/SDR prospect edits succeeded and both roles deleted disposable foreign-owned prospects.
- Browser as a test SDR: shared list shows own and foreign prospects; foreign prospect/account edit controls disabled; assigned prospect controls enabled; mobile account page checked at 390px after grid repair.

## Release state

Prepared on top of main 9f52b0d in an isolated worktree. Production deployment and production verification are pending. No message sent to Annie.
