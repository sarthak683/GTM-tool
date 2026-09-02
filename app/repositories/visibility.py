"""Explicit escape hatch for system work that has no requesting user.

Endpoint code must use each repository's ``visible_to`` entry point. Scheduled
tasks and lifecycle services use this dispatcher so an unscoped query is both
searchable in review and forced to carry a human-readable reason.
"""
from __future__ import annotations

from typing import TypeVar

from app.models.company import Company
from app.models.contact import Contact
from app.models.deal import Deal
from app.repositories.company import CompanyRepository
from app.repositories.contact import ContactRepository
from app.repositories.deal import DealRepository


ScopedEntity = TypeVar("ScopedEntity", Company, Contact, Deal)


def unscoped_for_background_job(model: type[ScopedEntity], reason: str):
    """Return every row for deliberate system work, never a user request."""
    if model is Company:
        return CompanyRepository.unscoped_for_background_job(reason)
    if model is Contact:
        return ContactRepository.unscoped_for_background_job(reason)
    if model is Deal:
        return DealRepository.unscoped_for_background_job(reason)
    raise TypeError(f"Unsupported visibility-scoped model: {model!r}")
