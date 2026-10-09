"""Viewing or owning an account never grants the right to change its owners."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks, HTTPException
from app.models.company import Company, CompanyUpdate
from app.models.user import User
from app.services.record_access import authorize_account_assignment, authorize_account_owner_update

@pytest.mark.parametrize('role', ['admin', 'superadmin', 'ae', 'sdr', 'agency', 'marketing'])
def test_assignment_role_matrix(role):
    actor = User(role=role)
    if actor.is_admin:
        authorize_account_assignment(actor)
    else:
        with pytest.raises(HTTPException) as exc:
            authorize_account_assignment(actor)
        assert exc.value.status_code == 403

@pytest.mark.parametrize('role', ['ae', 'sdr'])
@pytest.mark.parametrize('route', ['single', 'bulk', 'filter'])
@pytest.mark.parametrize('target', ['self', 'other', 'release'])
async def test_account_assignment_denied_before_lookup(role, route, target):
    from app.api.v1.endpoints import assignments as a
    actor = User(role=role)
    user_id = actor.id if target == 'self' else uuid4() if target == 'other' else None
    session = SimpleNamespace(execute=AsyncMock())
    with pytest.raises(HTTPException) as exc:
        if route == 'single':
            await a.assign_company(uuid4(), a.AssignRequest(user_id=user_id, role=role), session, actor, BackgroundTasks())
        elif route == 'bulk':
            await a.bulk_assign_companies(a.BulkAssignRequest(ids=[uuid4()], user_id=user_id, role=role), session, actor, BackgroundTasks())
        else:
            await a.bulk_assign_companies_by_filter(a.FilterAssignRequest(user_id=user_id, role=role), session, actor, BackgroundTasks(), a.CompanySourcingFilters())
    assert exc.value.status_code == 403
    session.execute.assert_not_awaited()

@pytest.mark.parametrize('route', ['update_company', 'patch_company', 'update_sourced_company'])
@pytest.mark.parametrize('field', ['assigned_to_id', 'sdr_id', 'assigned_rep', 'assigned_rep_email', 'assigned_rep_name', 'sdr_email', 'sdr_name'])
async def test_owned_account_cannot_be_reassigned_through_edit(monkeypatch, route, field):
    from app.api.v1.endpoints import companies, account_sourcing
    actor = User(role='ae')
    company = Company(name='Owned', assigned_to_id=actor.id)
    module = account_sourcing if route == 'update_sourced_company' else companies
    monkeypatch.setattr(module.CompanyRepository, 'get_or_raise', AsyncMock(return_value=company))
    payload = CompanyUpdate(**{field: uuid4() if field.endswith('_id') else 'Another owner'})
    session = SimpleNamespace(add=AsyncMock(), commit=AsyncMock())
    with pytest.raises(HTTPException) as exc:
        if route == 'update_sourced_company':
            await module.update_sourced_company(company.id, payload, actor, session)
        else:
            await getattr(module, route)(company.id, payload, session, actor)
    assert exc.value.status_code == 403
    assert company.assigned_to_id == actor.id and company.sdr_id is None
    session.commit.assert_not_awaited()

def test_normal_edits_and_unchanged_owner_echo_still_allowed():
    actor = User(role='sdr')
    company = Company(name='Owned', sdr_id=actor.id)
    authorize_account_owner_update(actor, company, {'name': 'New name', 'sdr_id': actor.id})
    authorize_account_owner_update(User(role='admin'), company, {'sdr_id': uuid4()})

async def test_contact_assignment_does_not_backfill_account_for_nonadmin():
    from app.api.v1.endpoints.assignments import _apply_contact_assignment
    from app.models.contact import Contact
    actor = User(role='ae')
    company = Company(name='Owned', assigned_to_id=actor.id)
    contact = Contact(company_id=company.id, assigned_to_id=actor.id)
    session = SimpleNamespace(add=lambda _: None)
    changed = await _apply_contact_assignment(session, contact, actor, is_sdr=True, current_assigned_id=None, company_cache={company.id: company}, actor=actor)
    assert not changed
    assert company.sdr_id is None
