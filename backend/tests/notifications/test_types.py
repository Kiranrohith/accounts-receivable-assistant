"""The notification list is built from rows of every type the system writes: the response schema must know them all,
or one row of a missing type fails the whole list."""

import uuid
from datetime import UTC, datetime
from typing import get_args

import pytest

from data.models.postgres.enums import NotificationType
from schemas.notifications.notifications import NotificationOut, NotificationTypeName


def test_the_response_schema_lists_every_notification_type():
    assert set(get_args(NotificationTypeName)) == {t.value for t in NotificationType}


@pytest.mark.parametrize("kind", [t.value for t in NotificationType])
def test_a_notification_of_every_type_serialises(kind):
    row = NotificationOut(
        notification_id=uuid.uuid4(), type=kind, message="m", entity_type="reminder", entity_id=uuid.uuid4(),
        is_read=False, created_at=datetime.now(UTC),
    )
    assert row.type == kind
