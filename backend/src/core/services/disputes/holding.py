"""Which disputes freeze money (D5 of the design). Pure rules, no I/O.

A dispute holds the payments aimed at its invoice while it is uncategorised, filed as Other, or
about the payment itself (the customer is withholding or paying less on purpose). A dispute about
quality, delivery and the like does not: the money is the customer's to pay and ours to apply.

Several open disputes on one invoice are ONE hold: the invoice's money is held while ANY of its
open disputes holds, and the hold is pinned to the oldest of them.
"""

from collections.abc import Iterable
from typing import Any

from data.models.postgres.enums import DisputeCategory, DisputeStatus


def holds_money(category: DisputeCategory | None, payment_related: bool) -> bool:
    return payment_related or category is None or category is DisputeCategory.OTHER


def is_holding(dispute: Any) -> bool:
    """An open dispute that freezes payments."""
    return dispute.status is not DisputeStatus.RESOLVED and holds_money(dispute.category, dispute.payment_related)


def holding_in_order(disputes: Iterable[Any]) -> list[Any]:
    """The holding disputes, the oldest first: the one a hold is pinned to comes first."""
    return sorted(
        (d for d in disputes if is_holding(d)),
        key=lambda d: (d.received_at, d.created_at, str(d.id)),
    )
