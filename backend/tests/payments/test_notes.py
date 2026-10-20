from core.services.payments.notes import format_note_number


def test_note_numbers_are_quotable_and_sortable():
    assert format_note_number(1, 2026) == "CN-2026-000001"
    assert format_note_number(123456, 2027) == "CN-2027-123456"
    assert format_note_number(1234567, 2026) == "CN-2026-1234567"  # never truncated
