from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import push
from app.models.contact import Contact


async def test_alternate_number_is_sent_to_phone(monkeypatch):
    contact = Contact(id=uuid4(), first_name='Test', last_name='Prospect', phone='+1 202-555-0100',
                      additional_phones=[{'number': '+1 202-555-0101', 'label': 'Mobile'}])
    monkeypatch.setattr(push, 'get_actionable_contact', AsyncMock(return_value=contact))
    sender = AsyncMock(return_value={'sent': 1, 'removed': 0, 'total': 1, 'configured': 1})
    monkeypatch.setattr(push, 'send_to_user', sender)
    await push.ring_mobile(contact.id, SimpleNamespace(id=uuid4()), object(),
                           push.RingMobilePayload(phone='+1 (202) 555-0101'))
    assert sender.call_args.args[2]['tel'] == '+12025550101'


async def test_unstored_number_cannot_be_sent(monkeypatch):
    contact = Contact(id=uuid4(), first_name='Test', last_name='Prospect', phone='+12025550100')
    monkeypatch.setattr(push, 'get_actionable_contact', AsyncMock(return_value=contact))
    sender = AsyncMock()
    monkeypatch.setattr(push, 'send_to_user', sender)
    with pytest.raises(HTTPException) as exc:
        await push.ring_mobile(contact.id, SimpleNamespace(id=uuid4()), object(),
                               push.RingMobilePayload(phone='+12025550999'))
    assert exc.value.status_code == 422
    sender.assert_not_awaited()


async def test_legacy_ring_request_still_uses_primary(monkeypatch):
    contact = Contact(id=uuid4(), first_name='Test', last_name='Prospect', phone='m+1 202-555-0100')
    monkeypatch.setattr(push, 'get_actionable_contact', AsyncMock(return_value=contact))
    sender = AsyncMock(return_value={'sent': 1, 'removed': 0, 'total': 1, 'configured': 1})
    monkeypatch.setattr(push, 'send_to_user', sender)
    await push.ring_mobile(contact.id, SimpleNamespace(id=uuid4()), object())
    assert sender.call_args.args[2]['tel'] == '+12025550100'


async def test_subscription_list_is_scoped_to_caller():
    user_id = uuid4()
    result = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))
    session = SimpleNamespace(execute=AsyncMock(return_value=result))
    assert await push.list_subscriptions(SimpleNamespace(id=user_id), session) == []
    stmt = session.execute.call_args.args[0]
    assert user_id in stmt.compile().params.values()


async def test_push_http_work_does_not_block_async_requests(monkeypatch):
    from app.services import push as service
    from app.models.push_subscription import PushSubscription
    sub = PushSubscription(user_id=uuid4(), endpoint='https://push.example.invalid/test', p256dh='test', auth='test')
    result = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [sub]))
    session = SimpleNamespace(execute=AsyncMock(return_value=result), add=Mock(), commit=AsyncMock())
    monkeypatch.setattr(service, 'vapid_configured', lambda: True)
    offload = AsyncMock(return_value=(True, 201))
    monkeypatch.setattr(service.asyncio, 'to_thread', offload)
    summary = await service.send_to_user(session, sub.user_id, {'type': 'test'})
    offload.assert_awaited_once_with(service._send_one, sub, {'type': 'test'})
    assert summary['sent'] == 1
