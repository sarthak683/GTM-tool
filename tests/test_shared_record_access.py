"""Shared viewing must never become shared write access."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks, HTTPException

from app.models.company import Company, CompanyUpdate
from app.models.contact import Contact, ContactUpdate
from app.models.user import User
from app.services.permissions import can_view_all_prospects
from app.services.record_access import authorize_company_edit, authorize_prospect_delete


@pytest.mark.parametrize('role', ['admin', 'superadmin', 'ae', 'sdr', 'marketing', 'agency'])
async def test_every_role_can_view_all_prospects(role):
    session = SimpleNamespace(get=AsyncMock())
    assert await can_view_all_prospects(session, User(role=role))
    session.get.assert_not_awaited()


@pytest.mark.parametrize('role', ['admin', 'superadmin', 'ae', 'sdr', 'marketing', 'agency'])
def test_prospect_delete_role_matrix(role):
    user = User(role=role)
    if role in {'admin', 'superadmin', 'ae', 'sdr'}:
        authorize_prospect_delete(user)
    else:
        with pytest.raises(HTTPException) as exc:
            authorize_prospect_delete(user)
        assert exc.value.status_code == 403


@pytest.mark.parametrize('role', ['admin', 'superadmin', 'ae', 'sdr', 'marketing', 'agency'])
@pytest.mark.parametrize('owned', [True, False])
def test_account_edit_role_matrix(role, owned):
    user = User(role=role)
    company = Company(name='Example', assigned_to_id=user.id if owned else uuid4())
    if owned or user.is_admin:
        authorize_company_edit(user, company)
    else:
        with pytest.raises(HTTPException) as exc:
            authorize_company_edit(user, company)
        assert exc.value.status_code == 403


@pytest.mark.parametrize('name', ['update_company', 'patch_company'])
async def test_account_endpoints_deny_before_writing(monkeypatch, name):
    from app.api.v1.endpoints import companies
    user = User(role='ae')
    company = Company(name='Foreign', assigned_to_id=uuid4())
    monkeypatch.setattr(companies.CompanyRepository, 'get_or_raise', AsyncMock(return_value=company))
    write = AsyncMock()
    monkeypatch.setattr(companies, '_apply_company_update', write)
    with pytest.raises(HTTPException) as exc:
        await getattr(companies, name)(company.id, CompanyUpdate(name='Overwrite'), SimpleNamespace(), user)
    assert exc.value.status_code == 403
    write.assert_not_awaited()
    assert company.name == 'Foreign'


async def test_sourcing_account_endpoint_does_not_overwrite_foreign_record(monkeypatch):
    from app.api.v1.endpoints import account_sourcing
    company = Company(name='Foreign', assigned_to_id=uuid4())
    monkeypatch.setattr(account_sourcing.CompanyRepository, 'get_or_raise', AsyncMock(return_value=company))
    with pytest.raises(HTTPException) as exc:
        await account_sourcing.update_sourced_company(company.id, CompanyUpdate(name='Overwrite'), User(role='sdr'), SimpleNamespace())
    assert exc.value.status_code == 403
    assert company.name == 'Foreign'


@pytest.mark.parametrize('kind', ['company', 'contact'])
async def test_assignment_cannot_be_used_to_claim_foreign_record(kind):
    from app.api.v1.endpoints import assignments
    actor = User(role='ae')
    record = Company(name='Foreign', assigned_to_id=uuid4()) if kind == 'company' else Contact(assigned_to_id=uuid4())
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: record)))
    args = [record.id, assignments.AssignRequest(user_id=actor.id), session, actor]
    if kind == 'company':
        args.append(BackgroundTasks())
    with pytest.raises(HTTPException) as exc:
        await getattr(assignments, 'assign_' + kind)(*args)
    assert exc.value.status_code == 403
    assert record.assigned_to_id != actor.id
    session.execute.assert_awaited_once()


@pytest.mark.parametrize('role', ['ae', 'sdr'])
async def test_rep_can_delete_foreign_prospect(monkeypatch, role):
    from app.api.v1.endpoints import contacts
    contact = Contact(assigned_to_id=uuid4())
    monkeypatch.setattr(contacts, 'get_visible_contact', AsyncMock(return_value=contact))
    delete = AsyncMock()
    monkeypatch.setattr(contacts.ContactRepository, 'delete_with_cascade', delete)
    await contacts.delete_contact(contact.id, SimpleNamespace(), User(role=role))
    delete.assert_awaited_once_with(contact.id)
