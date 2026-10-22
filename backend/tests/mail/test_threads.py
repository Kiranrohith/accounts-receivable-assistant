"""The signals that tie a mail to a conversation of ours (B7). The lookups themselves need the database and are
proved by the mail suites; here are the pure parts."""

import uuid

from core.services.mail.context import MailContext
from core.services.mail.parse import ParsedMail
from core.services.mail.threads import _REFERENCE, ThreadLink, message_ids_of


def mail(in_reply_to=None, references=(), subject="Re: your query") -> ParsedMail:
    return ParsedMail(None, "ap@client.in", None, (), subject, None, in_reply_to, tuple(references), "ok", [])


def test_the_message_ids_a_mail_answers_come_in_order_without_repeats():
    found = message_ids_of(mail("<c@x>", ["<a@x>", "<b@x>", "<c@x>"]))
    assert found == ["<c@x>", "<a@x>", "<b@x>"]


def test_a_mail_that_answers_nothing_has_no_ids():
    assert message_ids_of(mail()) == []


def test_an_in_reply_to_with_several_ids_is_split():
    assert message_ids_of(mail("<a@x> <b@x>")) == ["<a@x>", "<b@x>"]


def test_the_dispute_reference_is_found_in_a_subject_in_any_case():
    assert _REFERENCE.search("Re: Your query [DSP-1A2B3C4D]").group(1) == "1A2B3C4D"
    assert _REFERENCE.search("fwd: dsp-1a2b3c4d please").group(1) == "1a2b3c4d"
    assert not _REFERENCE.search("DSP-1A2B3C4 (one short) and XDSP-1A2B3C4D")


def test_a_thread_counts_as_context_even_from_an_unknown_sender():
    link = ThreadLink(uuid.uuid4(), "DSP-1A2B3C4D", "Assigned", None, "a reply to our mail")
    assert MailContext(False, (), False, thread=link).has_context
    assert not MailContext(False, (), False).has_context


def test_a_link_describes_itself_for_the_history_and_the_step_log():
    link = ThreadLink(uuid.uuid4(), "DSP-1A2B3C4D", "Resolved", None, "the same mail thread")
    assert link.describe == "DSP-1A2B3C4D (Resolved), found by the same mail thread"
    assert link.as_json()["reference"] == "DSP-1A2B3C4D" and link.as_json()["invoice_id"] is None
