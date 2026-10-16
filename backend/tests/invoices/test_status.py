import pytest

from core.services.documents.queries import Outcome, plain_status


@pytest.mark.parametrize(
    "processing, outcome, expected",
    [
        ("Pending", Outcome(), "Processing"),
        ("Running", Outcome(), "Processing"),
        ("Succeeded", Outcome(), "Processing"),  # finished but no recorded outcome (yet)
        ("Failed", Outcome(), "Failed"),
        ("Succeeded", Outcome("invoice_created"), "Invoice created"),
        ("Succeeded", Outcome("po_created"), "Purchase order created"),
        ("Running", Outcome("rejected"), "Processing"),  # re-run after a send-back: old outcome is stale
        ("Succeeded", Outcome("review"), "Needs review"),
        ("Succeeded", Outcome("rejected"), "Rejected"),
    ],
)
def test_one_plain_label_per_upload(processing, outcome, expected):
    assert plain_status(processing, outcome) == expected
