import pytest
from jinja2 import UndefinedError

from core.services.mail import templates

REMINDER = dict(
    invoice_number="INV-1", invoice_date="2026-10-01", due_date="2026-10-31", balance="INR 900.00",
    days_past_due=16, customer_name="Zenith Softlabs", contact_name="Priya",
)
EVERY_EMAIL = {
    "reminder_gentle": REMINDER,
    "reminder_firm": REMINDER,
    "reminder_final": REMINDER,
    "credit_note_email": dict(note_number="CN-0001", note_amount="INR 5,000.00", note_reason="Overpayment",
                              customer_name="Zenith Softlabs", contact_name="Priya"),
    "status_update": dict(dispute_ref="DSP-AB12CD34", customer_name="Zenith Softlabs", contact_name="Priya"),
    "dispute_resolved": dict(dispute_ref="DSP-AB12CD34", customer_name="Zenith Softlabs", contact_name="Priya",
                             narrative="A credit of INR 5,000 has been issued."),
}


@pytest.mark.parametrize("name", sorted(EVERY_EMAIL))
def test_every_email_renders_with_its_own_variables(name):
    out = templates.render_email(name, **EVERY_EMAIL[name])
    assert out.subject and "\n" not in out.subject
    assert out.body.startswith(templates.company_context()["company_name"])
    assert "Dear Priya," in out.body
    assert "{{" not in out.body and "{%" not in out.body
    assert "do-not-reply" not in out.body.lower() and "do not reply" not in out.body.lower()
    assert "simply reply to this email" in out.body and "Accounts Receivable Team" in out.body  # the shared footer
    assert "\n\n\n" not in out.body and out.body.endswith("\n") and not out.body.endswith("\n\n")


def test_the_subjects_are_the_agreed_wording():
    assert templates.render_email("reminder_gentle", **REMINDER).subject == "Payment reminder — Invoice INV-1"
    assert templates.render_email("reminder_firm", **REMINDER).subject == "Second reminder — Invoice INV-1 (16 days past due)"
    assert templates.render_email("reminder_final", **REMINDER).subject == "Final reminder — Invoice INV-1 (16 days past due)"
    assert templates.render_email("credit_note_email", **EVERY_EMAIL["credit_note_email"]).subject == "Credit note CN-0001 issued — INR 5,000.00"
    assert templates.render_email("status_update", **EVERY_EMAIL["status_update"]).subject == "Update on your query — ref DSP-AB12CD34"
    assert templates.render_email("dispute_resolved", **EVERY_EMAIL["dispute_resolved"]).subject == "Resolution — your query on DSP-AB12CD34"


def test_the_reminders_quote_the_figures_and_escalate_in_tone():
    gentle = templates.render_email("reminder_gentle", **REMINDER).body
    firm = templates.render_email("reminder_firm", **REMINDER).body
    final = templates.render_email("reminder_final", **REMINDER).body
    for body in (gentle, firm, final):
        assert "INR 900.00" in body and "INV-1" in body and "2026-10-31" in body
    assert "friendly reminder" in gentle and "within the next 7 days" in firm and "final reminder" in final
    assert "collections team" in final and "legal" not in final.lower()
    assert "16 days" in firm and "16 days" in final and "16 days" not in gentle


def test_a_missing_contact_falls_back_to_the_customer_name():
    out = templates.render_email("dispute_resolved", **{**EVERY_EMAIL["dispute_resolved"], "contact_name": None})
    assert "Dear Zenith Softlabs," in out.body
    out = templates.render_email("dispute_resolved", **{**EVERY_EMAIL["dispute_resolved"], "contact_name": ""})
    assert "Dear Zenith Softlabs," in out.body


def test_a_variable_the_code_forgot_is_an_error_not_a_blank():
    broken = {k: v for k, v in REMINDER.items() if k != "balance"}
    with pytest.raises(UndefinedError):
        templates.render_email("reminder_gentle", **broken)


def test_the_credit_note_reason_is_optional():
    with_reason = templates.render_email("credit_note_email", **EVERY_EMAIL["credit_note_email"]).body
    without = templates.render_email("credit_note_email", **{**EVERY_EMAIL["credit_note_email"], "note_reason": ""}).body
    assert "(Overpayment)" in with_reason and "()" not in without and "in your\nfavour." in without


def test_the_footer_shows_the_contact_line_only_when_there_is_one(monkeypatch):
    base = templates.company_context()
    monkeypatch.setattr(templates, "company_context", lambda: {**base, "company_contact_line": ""})
    assert " · " not in templates.render_email("dispute_resolved", **EVERY_EMAIL["dispute_resolved"]).body
    monkeypatch.setattr(templates, "company_context", lambda: {**base, "company_contact_line": "ar@genworx.test"})
    assert f"{base['company_name']} · ar@genworx.test" in templates.render_email("dispute_resolved", **EVERY_EMAIL["dispute_resolved"]).body


def test_the_resolution_narrative_is_placed_verbatim_and_the_invitation_to_reply_is_there():
    out = templates.render_email("dispute_resolved", **EVERY_EMAIL["dispute_resolved"]).body
    assert "has\nbeen resolved. A credit of INR 5,000 has been issued." in out
    assert "reply\nto this email and we will take another look" in out


def test_the_note_documents_render():
    env = templates._env
    credit = env.get_template("notes/credit_note.html.j2").render(
        **templates.company_context(), note_number="CN-0001", issue_date="2026-10-21", customer_name="Zenith",
        customer_tax_id="27ABCDE1234F1Z5", customer_tax_id_label="GSTIN", note_amount="5,000.00", note_reason="Overpayment",
        linked_invoice_number="INV-1", linked_payment_reference="UTR-9",
    )
    assert "CREDIT NOTE" in credit and "CN-0001" in credit and "Please quote this number (CN-0001)" in credit
    debit = env.get_template("notes/debit_note.html.j2").render(
        **templates.company_context(), invoice_number="DN-1", invoice_date="2026-10-21", due_date="2026-11-20",
        customer_name="Zenith", customer_tax_id=None, customer_tax_id_label="Tax ID", lines=[{"description": "Extra work", "amount": "2,000.00"}],
        total="2,000.00", linked_reference="INV-1",
    )
    assert "DEBIT NOTE" in debit and "Extra work" in debit and "GSTIN" not in debit
