"""Which notes pay a leftover balance (pure)."""

import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from core.services.payments.credit import Credit, best_fit, note_status_after, plan_spends

D = Decimal
T0 = datetime(2026, 10, 1)


def note(number, remaining, age_days=0):
    return Credit(uuid.uuid4(), number, D(remaining), T0 + timedelta(days=age_days))


def amounts(spends):
    return [(s.note_number, s.amount) for s in spends]


def test_the_smallest_note_that_covers_the_balance_wins():
    a, b, c = note("CN-1", "200"), note("CN-2", "350"), note("CN-3", "500")
    spends = plan_spends([(uuid.uuid4(), D("300"))], [a, b, c])
    assert amounts(spends) == [("CN-2", D("300"))]  # 350 is the smallest that covers 300


def test_an_exact_cover_is_the_smallest_cover():
    a, b = note("CN-1", "300"), note("CN-2", "350")
    assert amounts(plan_spends([(uuid.uuid4(), D("300"))], [a, b])) == [("CN-1", D("300"))]


def test_when_nothing_covers_the_largest_notes_are_chained_first():
    a, b, c = note("CN-1", "500"), note("CN-2", "300"), note("CN-3", "200")
    spends = plan_spends([(uuid.uuid4(), D("700"))], [a, b, c])
    assert amounts(spends) == [("CN-1", D("500")), ("CN-2", D("200"))]  # 500, then 200 of the 300


def test_the_chain_stops_when_the_notes_run_out():
    a, b = note("CN-1", "100"), note("CN-2", "50")
    spends = plan_spends([(uuid.uuid4(), D("400"))], [a, b])
    assert amounts(spends) == [("CN-1", D("100")), ("CN-2", D("50"))]  # 250 short: stays partly paid


def test_ties_go_to_the_older_note_then_the_lower_number():
    older, newer = note("CN-9", "400", age_days=0), note("CN-1", "400", age_days=5)
    assert amounts(plan_spends([(uuid.uuid4(), D("100"))], [newer, older])) == [("CN-9", D("100"))]
    same_a, same_b = note("CN-2", "400"), note("CN-1", "400")
    assert amounts(plan_spends([(uuid.uuid4(), D("100"))], [same_a, same_b])) == [("CN-1", D("100"))]


def test_chain_ties_follow_the_same_order():
    a, b = note("CN-2", "300", age_days=1), note("CN-1", "300", age_days=0)
    assert amounts(plan_spends([(uuid.uuid4(), D("500"))], [a, b])) == [("CN-1", D("300")), ("CN-2", D("200"))]


def test_a_partial_use_leaves_the_rest_for_the_next_invoice():
    first, second = uuid.uuid4(), uuid.uuid4()
    only = note("CN-1", "500")
    spends = plan_spends([(first, D("300")), (second, D("400"))], [only])
    assert [(s.invoice_id, s.amount) for s in spends] == [(first, D("300")), (second, D("200"))]
    assert sum(s.amount for s in spends) == D("500")  # never more than the note holds


def test_the_second_invoice_sees_what_the_first_used():
    a, b = note("CN-1", "400"), note("CN-2", "400")
    first, second = uuid.uuid4(), uuid.uuid4()
    spends = plan_spends([(first, D("400")), (second, D("400"))], [a, b])
    assert [(s.note_number, s.invoice_id) for s in spends] == [("CN-1", first), ("CN-2", second)]


def test_a_specific_note_is_used_even_when_another_fits_better():
    quoted, better = note("CN-5", "900"), note("CN-6", "300")
    spends = plan_spends([(uuid.uuid4(), D("300"))], [quoted, better], specific=quoted)
    assert amounts(spends) == [("CN-5", D("300"))]


def test_a_specific_note_is_used_only_as_far_as_it_goes():
    quoted = note("CN-5", "120")
    assert amounts(plan_spends([(uuid.uuid4(), D("300"))], [quoted], specific=quoted)) == [("CN-5", D("120"))]


def test_no_notes_no_spend_and_nothing_to_cover_no_spend():
    assert plan_spends([(uuid.uuid4(), D("300"))], []) == []
    assert plan_spends([(uuid.uuid4(), D("0"))], [note("CN-1", "50")]) == []


def test_a_used_up_note_is_not_picked():
    spent, live = note("CN-1", "0"), note("CN-2", "100")
    assert amounts(plan_spends([(uuid.uuid4(), D("60"))], [spent, live])) == [("CN-2", D("60"))]


def test_best_fit_exposes_the_chain():
    a, b = note("CN-1", "100"), note("CN-2", "100")
    picks = best_fit(D("150"), {a.note_id: a.remaining, b.note_id: b.remaining}, [a, b])
    assert [(n.number, amt) for n, amt in picks] == [("CN-1", D("100")), ("CN-2", D("50"))]


def test_note_status_follows_the_remaining_amount():
    assert note_status_after(D("500"), D("500")) == "Open"
    assert note_status_after(D("120"), D("500")) == "PartiallyUsed"
    assert note_status_after(D("0.00"), D("500")) == "Closed"
