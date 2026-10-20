"""Tolerances, splitting and scoring (pure)."""

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from constants.constants import CONFIDENCE_AUTO_APPLY, MAX_SUBSET_INVOICES, TIE_MARGIN
from core.services.payments.matching import (
    Cand,
    ScoredSet,
    candidate_sets,
    choose,
    number_shape,
    score_set,
)
from core.services.payments.tolerance import (
    Target,
    classify,
    split_by_statement,
    split_over_set,
)
from data.models.postgres.enums import MatchedLayer, Tolerance

D = Decimal
PAID = date(2026, 10, 15)


def t(balance, n=None):
    return Target(uuid.uuid4(), D(balance))


# ---- tolerance classes ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("received", "balance", "foreign", "expected", "diff"),
    [
        ("900", "900", False, Tolerance.T1, "0.00"),
        ("900.80", "900", False, Tolerance.T1, "0.80"),  # inside the rounding tolerance, over
        ("899.20", "900", False, Tolerance.T1, "-0.80"),  # inside it, under
        ("901", "900", False, Tolerance.T1, "1.00"),  # the bound is inclusive
        ("901.01", "900", False, Tolerance.T3, "1.01"),  # an INR payment has no FX band
        ("898.99", "900", False, Tolerance.T4, "-1.01"),
        ("910", "1000", True, Tolerance.T2, "-90.00"),  # foreign: within 1.5%? no, 9% -> see below
    ],
)
def test_classify(received, balance, foreign, expected, diff):
    if (received, balance, foreign) == ("910", "1000", True):
        expected = Tolerance.T4  # 9% short is far outside the 1.5% band
        diff = "-90.00"
    fit = classify(D(received), D(balance), foreign=foreign)
    assert fit.tolerance is expected and fit.diff == D(diff)


def test_t2_fx_band_only_for_foreign_payments():
    assert classify(D("985"), D("1000"), foreign=True).tolerance is Tolerance.T2  # -1.5% exactly
    assert classify(D("1015"), D("1000"), foreign=True).tolerance is Tolerance.T2  # +1.5% exactly
    assert classify(D("984.99"), D("1000"), foreign=True).tolerance is Tolerance.T4
    assert classify(D("1015.01"), D("1000"), foreign=True).tolerance is Tolerance.T3
    assert classify(D("985"), D("1000"), foreign=False).tolerance is Tolerance.T4  # INR: none


def test_clears_balance_flag():
    assert classify(D("1000"), D("1000"), foreign=False).clears_balance
    assert classify(D("1100"), D("1000"), foreign=False).clears_balance
    assert not classify(D("900"), D("1000"), foreign=False).clears_balance


# ---- splitting one amount over a set -------------------------------------------------------


def test_exact_payment_clears_each_invoice_and_puts_no_residual_beyond_the_difference():
    a, b = t("400"), t("500")
    plan = split_over_set([a, b], D("900"), foreign=False)
    assert plan.overall is Tolerance.T1
    assert [s.allocated for s in plan.slices] == [D("400"), D("500")]
    assert [s.residual for s in plan.slices] == [None, D("0.00")]
    assert plan.credit == 0 and plan.unapplied == 0


def test_signed_residual_rides_on_the_last_invoice():
    a, b = t("400"), t("500")
    plan = split_over_set([a, b], D("900.60"), foreign=False)
    assert plan.slices[-1].residual == D("0.60") and plan.slices[0].residual is None
    short = split_over_set([a, b], D("899.50"), foreign=False)
    assert short.slices[-1].residual == D("-0.50")
    assert short.overall is Tolerance.T1


def test_overpayment_clears_everything_and_reports_the_excess():
    a, b = t("400"), t("500")
    plan = split_over_set([a, b], D("1000"), foreign=False)
    assert plan.overall is Tolerance.T3 and plan.credit == D("100.00") and plan.unapplied == 0
    assert [s.allocated for s in plan.slices] == [D("400"), D("500")]
    assert all(s.residual is None for s in plan.slices)
    assert plan.allocated_total + plan.credit + plan.unapplied == D("1000")


def test_the_identity_holds_for_every_outcome():
    a, b = t("400"), t("500")
    for received in ("900", "900.40", "899.60", "1000", "300", "450", "1500"):
        plan = split_over_set([a, b], D(received), foreign=False)
        residuals = sum((s.residual or D("0") for s in plan.slices), D("0"))
        assert plan.allocated_total + residuals + plan.credit + plan.unapplied == D(received), received


def test_short_payment_fills_oldest_first_and_stops():
    a, b, c = t("400"), t("500"), t("600")
    plan = split_over_set([a, b, c], D("700"), foreign=False)
    assert plan.overall is Tolerance.T4
    assert [(s.invoice_id, s.allocated) for s in plan.slices] == [(a.invoice_id, D("400")), (b.invoice_id, D("300"))]
    assert all(s.tolerance is Tolerance.T4 for s in plan.slices)


def test_excess_is_not_a_credit_when_an_unknown_invoice_was_quoted():
    plan = split_over_set([t("400")], D("1000"), foreign=False, excess_is_credit=False)
    assert plan.unapplied == D("600.00") and plan.credit == 0


# ---- splitting by the advice's own statement -----------------------------------------------


def test_stated_split_judges_each_invoice_on_its_own_amount():
    a, b = t("400"), t("500")
    plan = split_by_statement([a, b], [D("400"), D("450")], foreign=False)
    assert [s.allocated for s in plan.slices] == [D("400"), D("450")]
    assert [s.tolerance for s in plan.slices] == [Tolerance.T1, Tolerance.T4]
    assert plan.overall is Tolerance.T4 and plan.credit == 0 and plan.unapplied == 0


def test_stated_over_amount_on_one_invoice_is_an_excess():
    a, b = t("400"), t("500")
    plan = split_by_statement([a, b], [D("450"), D("500")], foreign=False)
    assert plan.slices[0].tolerance is Tolerance.T3 and plan.slices[0].allocated == D("400")
    assert plan.credit == D("50.00") and plan.overall is Tolerance.T3


def test_stated_amount_for_an_unknown_invoice_stays_unapplied():
    plan = split_by_statement([t("400")], [D("400")], foreign=False, unplaced=D("250"))
    assert plan.unapplied == D("250") and plan.credit == 0
    assert plan.overall is Tolerance.T3


def test_a_statement_can_carry_a_credit_and_an_unapplied_part_at_once():
    a, b = t("400"), t("500")
    plan = split_by_statement([a, b], [D("450"), D("500")], foreign=False, unplaced=D("250"))
    assert plan.credit == D("50.00") and plan.unapplied == D("250")  # kept apart, never merged


# ---- scoring -------------------------------------------------------------------------------


def cand(number, balance, due_in_days=10, rate="1"):
    return Cand(uuid.uuid4(), number, PAID + timedelta(days=due_in_days), D(balance), D(rate))


def score(layer, members, received, **kw):
    base = dict(
        foreign=False, payment_date=PAID, customer_has_one_open_invoice=False, quoted_shape_known=False
    )
    base.update(kw)
    return score_set(layer, tuple(members), D(received), **base)


def test_worked_example_l1_exact_gets_the_full_stack():
    # L1 60 + T1 35 + due within 60 days 5 (+ single open invoice 5 + known shape 3) = 108 -> 100
    s = score(MatchedLayer.L1, [cand("A", "900")], "900", customer_has_one_open_invoice=True, quoted_shape_known=True)
    assert s.raw == 108 and s.score == 100


def test_worked_example_case_4_l2_is_90():
    # L2 45 + T1 35 + 5 + 5 = 90: auto with a reason
    s = score(MatchedLayer.L2, [cand("A", "900")], "900", customer_has_one_open_invoice=True)
    assert s.raw == 90


def test_layer_base_and_fit_points():
    assert score(MatchedLayer.L3, [cand("A", "900", due_in_days=400)], "900").raw == 35 + 35
    assert score(MatchedLayer.L3, [cand("A", "900", due_in_days=400)], "700").raw == 35 + 15
    foreign = score(MatchedLayer.L1, [cand("A", "1000", due_in_days=400)], "990", foreign=True)
    assert foreign.fit.tolerance is Tolerance.T2 and foreign.raw == 60 + 30


def test_due_date_modifier_window_is_inclusive_and_symmetric():
    near = score(MatchedLayer.L3, [cand("A", "900", due_in_days=60)], "900")
    far = score(MatchedLayer.L3, [cand("A", "900", due_in_days=61)], "900")
    past = score(MatchedLayer.L3, [cand("A", "900", due_in_days=-60)], "900")
    assert near.raw - far.raw == 5 and past.raw == near.raw


def test_single_open_invoice_modifier_is_skipped_for_a_set():
    one = score(MatchedLayer.L2, [cand("A", "900")], "900", customer_has_one_open_invoice=True)
    both = score(MatchedLayer.L2, [cand("A", "400"), cand("B", "500")], "900", customer_has_one_open_invoice=True)
    assert one.raw - both.raw == 5


def test_due_date_of_a_set_uses_the_earliest_member():
    early = score(MatchedLayer.L3, [cand("A", "400", due_in_days=400), cand("B", "500", due_in_days=30)], "900")
    assert early.raw == 35 + 35 + 5  # B is within 60 days


def test_number_shape_ignores_the_digits():
    assert number_shape("INV-2026-0042") == number_shape(" inv-2027-9 ") == "INV-9-9"
    assert number_shape("A/12") != number_shape("A-12")


# ---- choosing ------------------------------------------------------------------------------


def scored(raw, label="X"):
    c = cand(label, "100")
    return ScoredSet((c,), D("100"), classify(D("100"), D("100"), foreign=False), raw)


def test_auto_apply_at_85_inclusive_and_alone():
    assert choose([scored(CONFIDENCE_AUTO_APPLY)]).auto
    assert not choose([scored(CONFIDENCE_AUTO_APPLY - 1)]).auto


def test_nothing_to_choose():
    assert choose([]).best is None and not choose([]).auto


def test_tie_margin_is_inclusive_and_on_raw_scores():
    best = scored(100, "A")
    assert choose([best, scored(100 - TIE_MARGIN, "B")]).tied_with  # exactly 10 apart: tie
    assert not choose([best, scored(100 - TIE_MARGIN - 1, "B")]).tied_with  # 11 apart: clear


def test_clamping_does_not_manufacture_a_tie():
    # raw 112 and raw 100 both DISPLAY as 100, but they are 12 apart on raw scores: no tie
    a, b = scored(112, "A"), scored(100, "B")
    assert a.score == b.score == 100
    decided = choose([a, b])
    assert decided.best is a and decided.tied_with == [] and decided.auto
    # raw 108 vs raw 100 are only 8 apart: a tie, however they display
    assert choose([scored(108, "A"), scored(100, "B")]).tied_with


def test_a_tie_blocks_auto_apply_even_for_a_high_score():
    decided = choose([scored(100, "A"), scored(95, "B")])
    assert decided.best.label == "A" and decided.tied_with and not decided.auto


def test_ties_are_deterministic():
    one = choose([scored(90, "B"), scored(90, "A")])
    two = choose([scored(90, "A"), scored(90, "B")])
    assert one.best.label == two.best.label == "A"


# ---- candidate sets ------------------------------------------------------------------------


def test_candidate_sets_are_singles_plus_exact_fitting_combinations():
    a, b, c = cand("A", "400"), cand("B", "500"), cand("C", "700")
    sets, too_many = candidate_sets([a, b, c], lambda members: D("900"), foreign=False)
    labels = {" + ".join(m.number for m in s) for s in sets}
    assert labels == {"A", "B", "C", "A + B"} and not too_many  # only A+B totals 900


def test_two_equal_subsets_both_appear_so_the_tie_can_be_seen():
    a, b, c, d = cand("A", "400"), cand("B", "500"), cand("C", "300"), cand("D", "600")
    sets, _ = candidate_sets([a, b, c, d], lambda members: D("900"), foreign=False)
    pairs = {" + ".join(m.number for m in s) for s in sets if len(s) == 2}
    assert pairs == {"A + B", "C + D"}  # C6: two equal subsets


def test_too_many_open_invoices_means_singles_only():
    many = [cand(f"I{n}", "100") for n in range(MAX_SUBSET_INVOICES + 1)]
    sets, too_many = candidate_sets(many, lambda members: D("200"), foreign=False)
    assert too_many and all(len(s) == 1 for s in sets)


def test_a_set_that_cannot_be_valued_is_dropped():
    a, b = cand("A", "400", rate="80"), cand("B", "500", rate="85")

    def received_for(members):
        rates = {m.rate for m in members}
        return D("900") if len(rates) == 1 else None

    sets, _ = candidate_sets([a, b], received_for, foreign=True)
    assert all(len(s) == 1 for s in sets)  # A + B mixes two frozen rates
