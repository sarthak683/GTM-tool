"""nginx routing for the Data Room SOP document.

``/data-room`` is both a React route and — since the Sales Lifecycle SOP shipped
— a real directory on disk. That collision is invisible in a unit test and
invisible in the browser (the page still renders *something*), so it is asserted
here against the config text instead.

The failure it guards: nginx's SPA fallback ends in ``try_files $uri $uri/
/index.html``. The ``$uri/`` term matches the directory, so ``/data-room`` 301s
to ``/data-room/`` and nginx serves that directory's ``index.html`` — the raw SOP
document, with no app shell, no navigation and no way back. The Data Room tab's
item grid, add form and preview modal all become unreachable while the page
still looks like it loaded correctly.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

NGINX_CONF = Path(__file__).resolve().parent.parent / "frontend" / "nginx.conf"
SOP_DOC = Path(__file__).resolve().parent.parent / "frontend" / "public" / "data-room" / "index.html"


@pytest.fixture(scope="module")
def conf() -> str:
    return NGINX_CONF.read_text()


def _location_bodies(conf: str) -> dict[str, str]:
    """{location-spec: body} for every `location` block, non-nested."""
    out: dict[str, str] = {}
    for match in re.finditer(r"location\s+(=\s*\S+|\S+)\s*\{([^}]*)\}", conf):
        out[re.sub(r"\s+", " ", match.group(1)).strip()] = match.group(2)
    return out


class TestSpaRouteSurvivesTheDirectory:
    def test_bare_route_is_pinned_to_the_app_shell(self, conf):
        blocks = _location_bodies(conf)
        assert "= /data-room" in blocks, (
            "/data-room needs an exact-match location; without it the SPA "
            "fallback's `$uri/` term resolves the public/data-room directory "
            "and serves the SOP document instead of the React page"
        )
        assert "/index.html" in blocks["= /data-room"]

    def test_trailing_slash_variant_is_pinned_too(self, conf):
        """nginx 301s the bare path to the trailing-slash one, so pinning only
        the first spelling fixes nothing — the redirect target still resolves
        the directory."""
        blocks = _location_bodies(conf)
        assert "= /data-room/" in blocks
        assert "/index.html" in blocks["= /data-room/"]

    def test_neither_route_falls_through_to_the_directory(self, conf):
        """`$uri` / `$uri/` in these blocks would reintroduce the collision."""
        blocks = _location_bodies(conf)
        for spec in ("= /data-room", "= /data-room/"):
            assert "$uri" not in blocks[spec], (
                f"{spec} must serve /index.html directly, not resolve $uri"
            )


class TestDocumentIsStillServedAndFailsLoudly:
    def test_document_url_has_its_own_exact_match(self, conf):
        assert "= /data-room/index.html" in _location_bodies(conf)

    def test_missing_document_404s_rather_than_serving_the_app(self, conf):
        """The generic fallback would answer 200 with the CRM's own index.html,
        so the iframe would render the whole app inside itself and look like
        stale content rather than a missing file."""
        body = _location_bodies(conf)["= /data-room/index.html"]
        assert "=404" in body

    def test_document_is_not_cached(self, conf):
        body = _location_bodies(conf)["= /data-room/index.html"]
        assert "no-store" in body


class TestDocumentContent:
    def test_the_sop_document_is_committed(self):
        assert SOP_DOC.exists(), (
            "frontend/public/data-room/index.html is missing — the Data Room "
            "Sales Lifecycle tab 404s without it"
        )

    def test_it_is_the_sop_and_not_a_placeholder(self):
        assert "Sales Lifecycle SOP" in SOP_DOC.read_text()

    def test_no_plaintext_credentials(self):
        """This file is served by nginx with NO authentication — verified
        against prod, where /assets/*.js returns 200 with no token. Anything
        written here is readable by anyone holding the URL, and Metabase is
        internet-reachable, so a shared login printed here is usable from
        anywhere."""
        text = SOP_DOC.read_text()
        patterns = [
            r"password\s*[:=]\s*\S",
            r"passwd\s*[:=]\s*\S",
            r"api[_-]?key\s*[:=]\s*\S",
            r"secret\s*[:=]\s*\S",
        ]
        found = [p for p in patterns if re.search(p, text, re.IGNORECASE)]
        assert not found, (
            f"credential-shaped text in a publicly served document: {found}. "
            "Point at the password manager instead of printing the value."
        )
