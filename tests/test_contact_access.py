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


async def test_account_owner_can_edit_visible_prospect():
    user = SimpleNamespace(id=uuid4(), role="ae", email="ae@beacon.li", name="AE")
    contact = Contact(
        id=uuid4(),
        company_id=uuid4(),
        assigned_to_id=uuid4(),
        sdr_id=uuid4(),
    )
    session = SimpleNamespace(execute=AsyncMock(return_value=_Result(first=(contact.company_id,))))

    await authorize_contact_edit(session, user, contact)

    session.execute.assert_awaited_once()


async def test_outsider_cannot_edit_owned_prospect():
    user = SimpleNamespace(id=uuid4(), role="ae", email="ae@beacon.li", name="AE")
    contact = Contact(
        id=uuid4(),
        company_id=uuid4(),
        assigned_to_id=uuid4(),
        sdr_id=uuid4(),
    )
    session = SimpleNamespace(execute=AsyncMock(side_effect=[_Result(first=None), _Result(first=None)]))

    with pytest.raises(HTTPException) as exc:
        await authorize_contact_edit(session, user, contact)

    assert exc.value.status_code == 403


async def test_claiming_unassigned_ae_slot_sets_owner_identity():
    user = SimpleNamespace(id=uuid4(), role="ae", email="ae@beacon.li", name="AE")
    contact = Contact(id=uuid4(), assigned_to_id=None, sdr_id=uuid4())
    session = SimpleNamespace(execute=AsyncMock())

    await authorize_contact_edit(session, user, contact)

    assert contact.assigned_to_id == user.id
    assert contact.assigned_rep_email == user.email
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
