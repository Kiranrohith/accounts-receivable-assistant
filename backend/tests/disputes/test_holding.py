from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from core.services.disputes import holding
from data.models.postgres.enums import DisputeCategory, DisputeStatus


def dispute(name, *, category=None, related=False, status=DisputeStatus.ASSIGNED, day=1):
    stamp = datetime(2026, 10, day, 4, 0, tzinfo=UTC)
    return SimpleNamespace(id=name, category=category, payment_related=related, status=status, received_at=stamp, created_at=stamp)


@pytest.mark.parametrize(
    ("category", "related", "expected"),
    [
        (None, False, True),  # uncategorised: nobody knows yet, so the money waits
        (DisputeCategory.OTHER, False, True),
        (DisputeCategory.QUALITY, True, True),  # about the payment itself
        (DisputeCategory.PRICING, True, True),
        (DisputeCategory.QUALITY, False, False),
        (DisputeCategory.DELIVERY, False, False),
        (DisputeCategory.TAX_DISCREPANCY, False, False),
        (DisputeCategory.ALREADY_PAID, False, False),
    ],
)
def test_which_disputes_freeze_money(category, related, expected):
    assert holding.holds_money(category, related) is expected


def test_a_resolved_dispute_holds_nothing():
    assert not holding.is_holding(dispute("a", category=DisputeCategory.OTHER, status=DisputeStatus.RESOLVED))
    assert holding.is_holding(dispute("b", related=True, status=DisputeStatus.ESCALATED))  # escalation preserves holds


def test_the_hold_is_pinned_to_the_oldest_holding_dispute_not_the_oldest_dispute():
    quality_but_oldest = dispute("q", category=DisputeCategory.QUALITY, day=1)
    older_holder = dispute("old", related=True, day=3)
    younger_holder = dispute("young", category=DisputeCategory.OTHER, day=5)
    ordered = holding.holding_in_order([younger_holder, quality_but_oldest, older_holder])
    assert [d.id for d in ordered] == ["old", "young"]


def test_no_holder_left_means_the_set_is_empty():
    rest = [dispute("q", category=DisputeCategory.QUALITY), dispute("r", category=DisputeCategory.OTHER, status=DisputeStatus.RESOLVED)]
    assert holding.holding_in_order(rest) == []
