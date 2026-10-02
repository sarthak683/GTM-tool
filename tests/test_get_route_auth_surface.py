"""Every CRM read route must reject missing auth, including nested routers."""
import re

from fastapi.routing import iter_route_contexts


_PUBLIC_GETS = {
    "/api/v1/auth/google/login",
    "/api/v1/auth/google/callback",
    "/api/v1/settings/email-sync/google/callback",
    "/api/v1/personal-email-sync/callback",
    "/api/v1/zippy/documents/{token}",  # capability token, checked by that handler
    "/metrics", "/zippy_outputs/{filename:path}", "/health", "/",
}


def test_crm_get_routes_require_auth(client):
    routes = [r for r in iter_route_contexts(client.app.routes)
              if getattr(r, "dependant", None) is not None and "GET" in (r.methods or set())
              and r.path not in _PUBLIC_GETS]
    paths = {r.path for r in routes}
    assert {"/api/v1/auth/me", "/api/v1/contacts/", "/api/v1/deals/"} <= paths
    for route in routes:
        path = re.sub(r"\{[^}]+\}", "00000000-0000-0000-0000-000000000000", route.path)
        result = client.get(path)
        assert result.status_code in {401, 403}, (path, result.status_code)
