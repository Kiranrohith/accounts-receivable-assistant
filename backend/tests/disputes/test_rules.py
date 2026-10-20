from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from core.services.disputes import queries, workflow
from core.services.disputes.create import is_confident
from data.models.postgres.enums import DisputeCategory, DisputeStatus


@pytest.mark.parametrize(
    ("category", "confidence", "expected"),
    [
        (DisputeCategory.PRICING, 75, True),
        (DisputeCategory.PRICING, 74, False),
        (DisputeCategory.OTHER, 100, True),
        (DisputeCategory.PRICING, None, False),
        (None, 100, False),
    ],
)
def test_a_category_is_accepted_from_75(category, confidence, expected):
    assert is_confident(category, confidence) is expected


def test_escalated_disputes_need_the_escalated_permission():
    assert workflow.work_permission(DisputeStatus.ESCALATED) == "disputes.work.escalated"
    for status in (DisputeStatus.RAISED, DisputeStatus.CATEGORISED, DisputeStatus.ASSIGNED, DisputeStatus.CLARIFICATION_SENT):
        assert workflow.work_permission(status) == "disputes.work.normal"


def _dispute(**kw):
    base = dict(
        status=DisputeStatus.ASSIGNED,
        received_at=datetime(2026, 10, 19, 6, 0, tzinfo=UTC),
        first_response_due=date(2026, 10, 21),
        first_response_at=None,
        resolution_due=date(2026, 11, 3),
        resolved_at=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_overdue_means_outstanding_past_the_date():
    d = _dispute()
    assert not queries.first_response_overdue(d, date(2026, 10, 21))  # the due day itself is still in time
    assert queries.first_response_overdue(d, date(2026, 10, 22))
    assert not queries.resolution_overdue(d, date(2026, 11, 3))
    assert queries.resolution_overdue(d, date(2026, 11, 4))


def test_a_response_already_sent_is_not_overdue():
    d = _dispute(first_response_at=datetime(2026, 10, 20, 6, 0, tzinfo=UTC))
    assert not queries.first_response_overdue(d, date(2026, 10, 30))


def test_a_resolved_dispute_is_never_overdue_and_its_age_stops():
    d = _dispute(status=DisputeStatus.RESOLVED, resolved_at=datetime(2026, 10, 22, 6, 0, tzinfo=UTC))
    assert not queries.first_response_overdue(d, date(2026, 12, 1))
    assert not queries.resolution_overdue(d, date(2026, 12, 1))
    assert queries.age_days(d, date(2026, 12, 1)) == 3


def test_age_counts_ist_calendar_days():
    d = _dispute()
    assert queries.age_days(d, date(2026, 10, 19)) == 0
    assert queries.age_days(d, date(2026, 10, 29)) == 10
    assert queries.age_days(d, date(2026, 10, 1)) == 0  # never negative
