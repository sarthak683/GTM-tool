"""Close request-owned async SDK clients on the event loop that used them."""
from contextlib import asynccontextmanager


@asynccontextmanager
async def closing_client(client):
    """Release connections on success, API failure, timeout, or cancellation.

    Celery tasks use a fresh event loop. Leaving cleanup to an SDK destructor
    can schedule HTTP connection cleanup after that loop has already closed.
    This helper is for owned clients, never shared or cached clients.
    """
    try:
        yield client
    finally:
        await client.close()
