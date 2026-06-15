"""Zippy's document generators — MOM, NDA (IN/US/SG), ad-hoc drafts.

Each module exposes a ``generate(...)`` coroutine returning a
:class:`GeneratedDocument` so the agent can present a link back to the user.
"""
from app.services.zippy_docs.base import GeneratedDocument, ZIPPY_OUTPUT_DIR

__all__ = ["GeneratedDocument", "ZIPPY_OUTPUT_DIR"]
