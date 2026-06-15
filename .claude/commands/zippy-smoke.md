Smoke test Zippy locally.

Steps:
1. Run `make ps`.
2. Open `http://localhost:8080`.
3. Authenticate with a local token if needed using `scripts/smoke/local-token.sh`.
4. Open Zippy.
5. Send a short message.
6. Verify the response renders.
7. Open history.
8. Verify pin/unpin, rename, delete confirmation, and delete.
9. Clean up smoke-test conversations.
10. Report exact pass/fail.

