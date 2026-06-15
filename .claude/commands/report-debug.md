Debug scheduled sales reports.

Rules:
- Start read-only in production.
- Do not resend email unless the user explicitly asks.
- Do not mutate report settings without explicit approval.

Check:
1. Celery beat logs.
2. Worker logs.
3. Gmail/report sender settings.
4. Recipient list and individual send status.
5. Last scheduled send keys.
6. Timezone assumptions.
7. Daily vs weekly report behavior.
8. Any OAuth/token refresh errors.

Report:
- whether the report was scheduled
- whether it attempted each recipient
- who received or failed
- root cause
- safest fix

