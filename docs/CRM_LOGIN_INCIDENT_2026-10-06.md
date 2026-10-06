# CRM login failure — October 6, 2026

## Confirmed cause

The production Google OAuth callback failed with SQLAlchemy QueuePool timeout
(size 10, overflow 20, 30-second wait). Production release 240 used backend
`v0.261006-e8002a5-merged1`. Inspection of the running endpoint showed that
`deal_board_stream` no longer accepted/closed the shared authentication session.
This reintroduced the October 2 leak: each open Pipeline SSE stream retains a DB
connection until disconnect, eventually preventing login and ordinary API reads.

The October 2 fix and regression tests were still uncommitted in this checkout.
The precise upstream merge/build step that omitted them was not established.

## Recovery and patch

A rolling backend restart cleared the exhausted pools. A minimal image extends
the exact current production image and replaces only `app/api/v1/endpoints/deals.py`.
It adds the shared `DBSession` parameter and closes it before streaming.

- Backend hotfix: `beacon.azurecr.io/gtm-be:v0.261006-e8002a5-streamfix2`.
- Image index: `sha256:53fbd63e5911a9e89fc972fa14b5a2ea3a8c4b2027ccf939f65464ed60131a1f`.
- amd64 manifest: `sha256:0871b0322b79f56fcb0090e0dd52d50cbc3b2016a9954eda81843c8128e1232e`.
- Frontend preserved at `v0.261006-e8002a5-merged1`.
- Local targeted regression suite: 12 passed.
- Staging release 271: 40 concurrent streams, zero retained authentication
  transactions; profile, board, and unread notification requests returned 200.
- Production three-way Helm diff: only five backend image fields changed.
  All seven live Deployment/StatefulSet workloads remain rendered.

Artifacts: `artifacts/crm-login-incident-2026-10-06/`.

## Release requirement

Carry the endpoint fix and `tests/test_board_stream_session.py` into the source
branch used for all subsequent releases. Do not replace this hotfix with a full
build that omits them. Run the lifecycle regression suite and the concurrent
stream probe before promotion; a health check alone does not detect this leak.

## Production verification

Production Helm release 241 completed. Every pod is ready with zero restarts;
both backends and all three Celery workloads have the hotfix digest.
Each backend passed 40 simultaneous streams using a short-lived diagnostic
impersonation token for `jacob@beacon.li` (token never printed or saved).
Both had zero retained authentication transactions. Profile, board and unread
notification reads returned 200; board reads took 0.773 and 0.777 seconds.
Public Google login initiation returns the expected 307 redirect.
The existing signed-in browser reloaded Pipeline with 746 visible deals and no
console errors. Jacob's own Google browser sign-in remains to be retried by him.
Fresh backend logs contained no QueuePool errors, unhandled exceptions or 500s.
