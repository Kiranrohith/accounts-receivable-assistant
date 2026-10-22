"""The manual upload gate is stricter than the mail-attachment filter, on purpose: a person who picked a
file must be told when it is refused, whatever its type."""

import pytest

from core.exceptions.errors import UploadRejected
from core.services.invoices.upload import check_upload


@pytest.mark.parametrize("name", ["empty.pdf", "empty.png", "empty.jpg", "empty.docx", "empty.webp"])
def test_a_zero_byte_file_is_refused_visibly_even_when_it_is_an_image(name):
    # (a mail attachment that is an empty image is merely skipped; here it is an error the uploader sees)
    with pytest.raises(UploadRejected, match="File is empty"):
        check_upload(name, b"")


def test_a_small_but_real_image_or_pdf_is_not_refused_for_its_size():
    check_upload("tiny.pdf", b"%PDF-1.4\n%%EOF")
    check_upload("small.png", b"\x89PNG\r\n\x1a\n" + b"x" * 50)
