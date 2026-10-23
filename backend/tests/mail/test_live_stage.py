from core.services.jobs import queue
from core.services.mail.live import stage_for


def test_a_job_that_has_logged_nothing_is_opening_the_file():
    assert stage_for(queue.JOB_INTAKE, None, "unknown") == "Opening the file"


def test_after_parsing_a_new_upload_is_still_being_told_apart_but_a_decided_one_is_read():
    assert stage_for(queue.JOB_INTAKE, "parse", "unknown") == "Working out what it is"
    assert stage_for(queue.JOB_PO, "parse", "po") == "Reading its details"
    assert stage_for(queue.JOB_INTAKE, "classify", "po") == "Reading its details"


def test_each_later_step_has_its_own_words_and_the_kind_decides_the_matching_one():
    assert stage_for(queue.JOB_INTAKE, "extract", "po") == "Checking the details"
    assert stage_for(queue.JOB_INTAKE, "validate", "po") == "Finding the customer"
    assert stage_for(queue.JOB_INTAKE, "customer_match", "po") == "Creating the purchase order"
    assert stage_for(queue.JOB_INTAKE, "customer_match", "invoice") == "Creating the invoice"
    assert stage_for(queue.JOB_INTAKE, "identify", "payment") == "Matching to invoices"
    assert stage_for(queue.JOB_INTAKE, "match", "payment") == "Saving it"
    assert stage_for(queue.JOB_INTAKE, "route", "po") == "Finishing"


def test_a_held_payment_matched_again_and_an_unknown_step_read_plainly():
    assert stage_for(queue.JOB_PAYMENT_REMATCH, "parse", "payment") == "Matching the payment again"
    assert stage_for(queue.JOB_INTAKE, "something_new", "po") == "Working on it"
