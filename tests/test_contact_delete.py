"""Delete authorization and real PostgreSQL cadence dependency regression."""
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.api.v1.endpoints import contacts
from app.core.exceptions import NotFoundError
from app.models.activity import Activity
from app.models.contact import Contact
from app.models.sequence import Enrollment, Sequence
from app.models.task import Task, TaskComment
from app.models.user import User
from app.repositories.contact import ContactRepository


@pytest.mark.parametrize("role", ["superadmin", "admin", "ae", "sdr"])
async def test_sales_roles_can_delete_prospect_owned_by_another_rep(monkeypatch, role):
    user = SimpleNamespace(id=uuid4(), role=role)
    prospect = Contact(id=uuid4(), first_name="Visible", last_name="Prospect", assigned_to_id=uuid4(), sdr_id=uuid4())
    visible = AsyncMock(return_value=prospect)
    remove = AsyncMock()
    monkeypatch.setattr(contacts, "get_visible_contact", visible)
    monkeypatch.setattr(ContactRepository, "delete_with_cascade", remove)
    session = SimpleNamespace()
    await contacts.delete_contact(prospect.id, session, user)
    visible.assert_awaited_once_with(session, user, prospect.id)
    remove.assert_awaited_once_with(prospect.id)


async def test_hidden_prospect_cannot_be_deleted(monkeypatch):
    monkeypatch.setattr(contacts, "get_visible_contact", AsyncMock(side_effect=NotFoundError("Contact not found")))
    remove = AsyncMock()
    monkeypatch.setattr(ContactRepository, "delete_with_cascade", remove)
    with pytest.raises(NotFoundError):
        await contacts.delete_contact(uuid4(), SimpleNamespace(), SimpleNamespace(role="sdr"))
    remove.assert_not_awaited()


@pytest.mark.parametrize("role", ["ae", "sdr"])
async def test_bulk_delete_keeps_visibility_scope(monkeypatch, role):
    visible, hidden = uuid4(), uuid4()
    monkeypatch.setattr(contacts, "get_visible_contact_ids", AsyncMock(return_value=[visible]))
    remove = AsyncMock(return_value=1)
    monkeypatch.setattr(ContactRepository, "delete_many", remove)
    result = await contacts.bulk_delete_selected_contacts(contacts.BulkDeleteRequest(ids=[visible, hidden]), SimpleNamespace(), SimpleNamespace(role=role))
    remove.assert_awaited_once_with([visible])
    assert result == {"deleted": 1, "requested": 2, "skipped_not_owned": 1}


@pytest.mark.skipif(os.environ.get("CONTACT_DELETE_DB_TESTS") != "1", reason="Opt-in local/staging PostgreSQL test")
@pytest.mark.parametrize("single", [False, True])
async def test_enrolled_prospect_deletes_without_touching_other_contacts(single):
    from app.config import settings
    assert settings.ENVIRONMENT != "production", "Never run fixture tests in production"
    engine = create_async_engine(settings.DATABASE_URL)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                async with AsyncSession(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False) as session:
                    user_id = (await session.execute(select(User.id).limit(1))).scalar_one()
                    target = Contact(first_name="QA", last_name="DeleteTarget")
                    keep = Contact(first_name="QA", last_name="DeleteKeep")
                    sequence = Sequence(name="QA delete regression", owner_id=user_id)
                    session.add_all([target, keep, sequence]); await session.flush()
                    enroll = Enrollment(contact_id=target.id, sequence_id=sequence.id, enrolled_by=user_id)
                    keep_enroll = Enrollment(contact_id=keep.id, sequence_id=sequence.id, enrolled_by=user_id)
                    session.add_all([enroll, keep_enroll]); await session.flush()
                    # Include an enrollment-linked task whose polymorphic entity
                    # does not point at the contact: both scopes must be cleaned.
                    tasks = [Task(entity_type="contact", entity_id=target.id, title="QA manual"), Task(entity_type="deal", entity_id=uuid4(), title="QA cadence", enrollment_id=enroll.id)]
                    activity = Activity(type="note", contact_id=target.id, content="Preserve history")
                    session.add_all([*tasks, activity]); await session.flush()
                    comments = [TaskComment(task_id=t.id, body="QA") for t in tasks]
                    session.add_all(comments); await session.flush()
                    target_id, keep_id, activity_id, sequence_id = target.id, keep.id, activity.id, sequence.id
                    enrollment_id, keep_enrollment_id = enroll.id, keep_enroll.id
                    task_ids, comment_ids = [t.id for t in tasks], [c.id for c in comments]
                    repo = ContactRepository(session)
                    if single:
                        await repo.delete_with_cascade(target_id)
                    else:
                        assert await repo.delete_many([target_id, target_id, uuid4()]) == 1
                    session.expunge_all()
                    assert await session.get(Contact, target_id) is None
                    assert await session.get(Enrollment, enrollment_id) is None
                    for tid in task_ids: assert await session.get(Task, tid) is None
                    for cid in comment_ids: assert await session.get(TaskComment, cid) is None
                    assert (await session.get(Activity, activity_id)).contact_id is None
                    assert await session.get(Contact, keep_id) is not None
                    assert await session.get(Enrollment, keep_enrollment_id) is not None
                    assert await session.get(Sequence, sequence_id) is not None
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
