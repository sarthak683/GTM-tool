from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from app.api.v1.endpoints.personal_email_sync import get_personal_email_status
from app.models.user_email_connection import UserEmailConnection


async def test_revoked_connection_retains_reconnect_guidance():
    user_id = uuid4()
    connection = UserEmailConnection(user_id=user_id, email_address='rep@example.invalid', token_data={},
                                     is_active=False, last_error='Reconnect Gmail: invalid_grant',
                                     last_sync_epoch=12345, backfill_completed=True)
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(
        scalars=lambda: SimpleNamespace(first=lambda: connection))))
    status = await get_personal_email_status(session, SimpleNamespace(id=user_id))
    assert not status.connected
    assert status.last_error == 'Reconnect Gmail: invalid_grant'
    assert status.email_address == 'rep@example.invalid'
    assert status.last_sync_epoch == 12345
    assert not status.has_send_scope
