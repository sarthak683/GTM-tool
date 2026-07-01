"""Shared pytest fixtures for the Beacon GTM CRM backend test suite.

The fixtures here are deliberately lightweight: they let us exercise *routing*
and *auth rejection* without standing up Postgres, Redis, Celery, or any of the
background workers the real app boots.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def client():
    """A FastAPI ``TestClient`` that does NOT run the application lifespan.

    Why no ``with TestClient(app) as client:``
    ------------------------------------------
    ``TestClient`` only triggers the app's lifespan (startup/shutdown) when it is
    used as a context manager. We intentionally construct it *without* ``with`` so
    the lifespan stays dormant. The lifespan in ``app.main`` does real,
    environment-dependent work that has no place in a unit/routing test:

      * ``settings.validate_runtime_secrets()`` (would raise in prod-like envs),
      * ``start_background_workers()`` (spawns async background jobs),
      * Instantly webhook registration (outbound HTTP),
      * the pre-meeting automation loop (an infinite ``asyncio`` task).

    Skipping the lifespan lets us assert on auth/routing in isolation. Endpoints
    that need a DB session still *resolve* the ``get_session`` dependency, but the
    auth guard (`app.core.dependencies.get_current_user`) rejects a missing
    ``Authorization`` header *before* any query runs, and merely opening an
    ``AsyncSession`` performs no I/O — so these tests need no live database.

    ``app`` is imported lazily inside the fixture (not at module import time) so
    that simply *collecting* this conftest never drags in the whole application
    object graph; only tests that actually request ``client`` pay that cost.
    """
    from fastapi.testclient import TestClient

    from app.main import app

    # No `with` block => lifespan startup/shutdown are skipped on purpose.
    # `raise_server_exceptions=False` makes the client return the mapped HTTP
    # response (e.g. 401 from UnauthorizedError) instead of re-raising, matching
    # how a real client over the wire would observe these endpoints.
    return TestClient(app, raise_server_exceptions=False)
