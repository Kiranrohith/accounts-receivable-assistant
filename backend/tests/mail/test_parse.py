from email.message import EmailMessage

from core.services.mail.parse import parse_mail

PDF = b"%PDF-1.4\n%fake\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 40


def build(**headers) -> EmailMessage:
    m = EmailMessage()
    m["From"] = headers.get("sender", "Anita Rao <AP@Client.IN>")
    m["To"] = "genworx.ardemo@gmail.com"
    m["Subject"] = headers.get("subject", "Payment INV-1001")
    m["Message-ID"] = "<abc@client.in>"
    m["Date"] = "Wed, 21 Oct 2026 10:00:00 +0530"
    if "in_reply_to" in headers:
        m["In-Reply-To"] = headers["in_reply_to"]
        m["References"] = "<r1@x> " + headers["in_reply_to"]
    return m


def test_sender_is_the_bare_lowercase_address_and_the_name_is_kept():
    m = build()
    m.set_content("Paid today.")
    p = parse_mail(m.as_bytes())
    assert p.sender == "ap@client.in" and p.sender_name == "Anita Rao"
    assert p.to == ("genworx.ardemo@gmail.com",) and p.subject == "Payment INV-1001"
    assert p.sent_at is not None and p.sent_at.year == 2026


def test_a_plain_body_and_a_pdf_attachment():
    m = build()
    m.set_content("Please find the advice.")
    m.add_attachment(PDF, maintype="application", subtype="pdf", filename="advice.pdf")
    p = parse_mail(m.as_bytes())
    assert p.body == "Please find the advice."
    assert [(a.file_name, a.data, a.inline) for a in p.attachments] == [("advice.pdf", PDF, False)]
    assert p.attachments[0].content_type == "application/pdf"


def test_an_html_only_mail_is_read_as_text():
    m = build()
    m.set_content("<html><body><p>Paid <b>INV-1001</b></p><script>x()</script></body></html>", subtype="html")
    p = parse_mail(m.as_bytes())
    assert "Paid INV-1001" in p.body and "x()" not in p.body


def test_an_inline_logo_with_a_content_id_is_marked_inline():
    m = build()
    m.set_content("see below")
    m.add_alternative("<p>hi <img src='cid:logo1'></p>", subtype="html")
    m.get_payload()[1].add_related(PNG, maintype="image", subtype="png", cid="<logo1>", filename="logo.png")
    p = parse_mail(m.as_bytes())
    logos = [a for a in p.attachments if a.file_name == "logo.png"]
    assert len(logos) == 1 and logos[0].inline and logos[0].data == PNG


def test_an_attachment_without_a_name_gets_one_from_its_type():
    m = build()
    m.set_content("x")
    m.add_attachment(PNG, maintype="image", subtype="png")
    p = parse_mail(m.as_bytes())
    assert p.attachments[0].file_name.endswith(".png")


def test_an_empty_attachment_is_kept_so_it_can_be_rejected_visibly():
    m = build()
    m.set_content("x")
    m.add_attachment(b"", maintype="application", subtype="pdf", filename="Remittance.pdf")
    p = parse_mail(m.as_bytes())
    assert p.attachments[0].data == b"" and p.attachments[0].file_name == "Remittance.pdf"


def test_a_spreadsheet_is_an_attachment_too():
    m = build()
    m.set_content("x")
    m.add_attachment(
        b"PK\x03\x04", maintype="application", subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="advice.xlsx",
    )
    assert parse_mail(m.as_bytes()).attachments[0].file_name == "advice.xlsx"


def test_reply_headers_are_kept_for_thread_linking():
    m = build(in_reply_to="<sent1@genworx>")
    m.set_content("paid yesterday")
    p = parse_mail(m.as_bytes())
    assert p.in_reply_to == "<sent1@genworx>" and p.references == ("<r1@x>", "<sent1@genworx>")


def test_a_mail_without_a_sender_or_subject_still_parses():
    m = EmailMessage()
    m.set_content("hello")
    p = parse_mail(m.as_bytes())
    assert p.sender is None and p.subject == "" and p.body == "hello" and p.attachments == []
