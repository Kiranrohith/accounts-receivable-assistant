"""Attachment qualification at triage, pure. Illustrative cases; the answer key is the seed kit."""

import uuid

from core.services.mail.attachments import Attachment, MailRef, Verdict, classify_attachment, qualify

PDF = b"%PDF-1.4\n" + b"x" * 2600  # a valid small ERP-style PDF, 2.6 KB
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 6000
JPG = b"\xff\xd8\xff\xe0" + b"x" * 6000


MAIL = MailRef("18c0ffee12345", uuid.UUID("11111111-2222-3333-4444-555555555555"))


def decide(name, data, **kw):
    return classify_attachment(Attachment(name, data, **kw), MAIL)


def test_a_small_pdf_qualifies_the_size_floor_is_for_images_only():  # 
    d = decide("advice.pdf", PDF)
    assert len(PDF) < 5 * 1024 and d.verdict is Verdict.QUALIFY
    assert decide("tiny.pdf", b"%PDF-1").verdict is Verdict.QUALIFY  # any non-zero size


def test_an_empty_pdf_is_a_rejected_attachment_not_a_job():  # / P62
    d = decide("advice.pdf", b"")
    assert d.verdict is Verdict.REJECT and d.reason == "attachment is empty"


def test_content_that_is_not_what_the_name_says_is_rejected():
    for name, data in (("a.pdf", b"<html>not a pdf"), ("a.png", b"%PDF-1.4" + b"x" * 6000), ("a.jpg", PNG)):
        d = decide(name, data)
        assert d.verdict is Verdict.REJECT and d.reason == "file content does not match its type", name


def test_real_images_over_the_floor_qualify_and_signatures_are_checked():
    assert decide("scan.png", PNG).verdict is Verdict.QUALIFY
    assert decide("scan.jpg", JPG).verdict is Verdict.QUALIFY
    assert decide("scan.jpeg", JPG).verdict is Verdict.QUALIFY


def test_inline_images_and_small_images_are_skipped_with_a_reason():
    assert decide("logo.png", PNG, inline=True).verdict is Verdict.SKIP
    assert decide("logo.png", PNG, content_type="image/png", inline=True).reason.startswith("inline image")
    small = decide("icon.png", b"\x89PNG\r\n\x1a\n" + b"x" * 100)
    assert small.verdict is Verdict.SKIP and "under 5 KB" in small.reason
    assert decide("pixel.gif", b"GIF89a").verdict is Verdict.SKIP
    assert decide("sig", b"x" * 100, content_type="image/jpeg").verdict is Verdict.SKIP  # typed by content-type


def test_the_order_is_skip_before_reject_for_images():
    # an empty image is "an image under 5 KB" (step 2) before it is "an empty file" (step 3)
    assert decide("a.png", b"").verdict is Verdict.SKIP
    assert decide("a.pdf", b"").verdict is Verdict.REJECT


def test_a_type_we_do_not_read_is_rejected_visibly_not_dropped():
    d = decide("advice.xlsx", b"PK\x03\x04" + b"x" * 9000)
    assert d.verdict is Verdict.REJECT and "not supported" in d.reason
    assert decide("big.gif", b"GIF89a" + b"x" * 9000).verdict is Verdict.REJECT


def test_a_corrupt_pdf_with_a_good_signature_still_qualifies_it_fails_at_parse():
    assert decide("locked.pdf", b"%PDF-1.7\n" + b"\x00" * 3000).verdict is Verdict.QUALIFY  # case 9 / A1


def test_the_whole_mail():
    result = qualify(
        [
            Attachment("logo.png", PNG, inline=True),
            Attachment("advice.pdf", b""),
            Attachment("remittance.pdf", PDF),
            Attachment("icon.png", b"\x89PNG\r\n\x1a\n"),
        ],
        MAIL,
    )
    assert [d.attachment.file_name for d in result.qualifying] == ["remittance.pdf"]
    assert [d.attachment.file_name for d in result.skipped] == ["logo.png", "icon.png"]
    assert [d.attachment.file_name for d in result.rejected] == ["advice.pdf"]
    assert not result.body_only
    only_bad = qualify([Attachment("advice.pdf", b"")], MAIL)  # nothing usable -> read the mail body
    assert only_bad.body_only and len(only_bad.rejected) == 1


def test_a_rejection_carries_the_reason_and_the_way_back_to_the_source_mail():
    log = decide("advice.pdf", b"").as_log()
    assert log["verdict"] == "reject" and log["reason"] == "attachment is empty" and log["size_bytes"] == 0
    assert log["source_mail"] == {
        "gmail_message_id": "18c0ffee12345",
        "ingested_email_id": "11111111-2222-3333-4444-555555555555",
        "link": "https://mail.google.com/mail/u/0/#all/18c0ffee12345",
    }
    # skips lead back to the mail too, for the step log
    assert decide("logo.png", PNG, inline=True).as_log()["source_mail"]["link"].endswith("18c0ffee12345")


def test_every_decision_in_a_whole_mail_names_its_source():
    result = qualify([Attachment("a.pdf", b""), Attachment("b.pdf", PDF), Attachment("c.png", PNG, inline=True)], MAIL)
    for d in [*result.qualifying, *result.skipped, *result.rejected]:
        assert d.as_log()["source_mail"]["gmail_message_id"] == "18c0ffee12345"
