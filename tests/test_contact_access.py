from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.models.contact import Contact
from app.services.contact_access import authorize_contact_edit, get_visible_contact_ids


class _Result:
    def __init__(self, *, first=None, scalars=None):
        self._first = first
        self._scalars = scalars or []

    def first(self):
        return self._first

    def scalars(self):
        return SimpleNamespace(all=lambda: self._scalars)


@pytest.mark.parametrize("role", ["admin", "superadmin", "ae", "sdr", "marketing", "agency"])
@pytest.mark.parametrize("slot", ["assigned_to_id", "sdr_id", "neither"])
async def test_edit_requires_record_assignment_or_admin(role, slot):
    user = SimpleNamespace(id=uuid4(), role=role)
    contact = Contact(id=uuid4(), company_id=uuid4(), assigned_to_id=uuid4(), sdr_id=uuid4())
    if slot != "neither":
        setattr(contact, slot, user.id)
    session = SimpleNamespace(execute=AsyncMock())
    before = contact.model_dump()
    if slot != "neither" or role in {"admin", "superadmin"}:
        await authorize_contact_edit(session, user, contact)
    else:
        with pytest.raises(HTTPException) as exc:
            await authorize_contact_edit(session, user, contact)
        assert exc.value.status_code == 403
    assert contact.model_dump() == before
    session.execute.assert_not_awaited()


@pytest.mark.parametrize("role", ["ae", "sdr"])
async def test_unassigned_contact_is_not_automatically_claimed(role):
    user = SimpleNamespace(id=uuid4(), role=role)
    contact = Contact(id=uuid4(), company_id=uuid4())
    session = SimpleNamespace(execute=AsyncMock())
    with pytest.raises(HTTPException) as exc:
        await authorize_contact_edit(session, user, contact)
    assert exc.value.status_code == 403
    assert contact.assigned_to_id is None and contact.sdr_id is None
    session.execute.assert_not_awaited()


async def test_visible_ids_preserve_input_order_and_remove_duplicates(monkeypatch):
    first, second, hidden = uuid4(), uuid4(), uuid4()
    monkeypatch.setattr(
        "app.services.contact_access.visible_contact_restriction",
        AsyncMock(return_value=None),
    )
    session = SimpleNamespace(execute=AsyncMock(return_value=_Result(scalars=[second, first])))
    user = SimpleNamespace(id=uuid4(), role="admin")

    result = await get_visible_contact_ids(
        session,
        user,
        [first, hidden, first, second],
    )

    assert result == [first, second]
