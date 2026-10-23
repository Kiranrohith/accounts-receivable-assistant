"""The Settings read-out: it shows the live constants, in words a person reads, and lists nothing that does not exist."""

from datetime import time
from decimal import Decimal

from constants import constants
from core.services import settings_view


def test_every_listed_constant_exists_in_the_code():
    for _, _, items in settings_view._SECTIONS:
        for name, label, description in items:
            assert hasattr(constants, name), name
            assert label and description, name


def test_no_constant_is_listed_twice():
    names = [name for _, _, items in settings_view._SECTIONS for name, _, _ in items]
    assert len(names) == len(set(names))


def test_values_are_the_running_ones_not_copies():
    out = settings_view.build()
    shown = {i.name: i.value for s in out.sections for i in s.items}
    assert shown["CONFIDENCE_AUTO_APPLY"] == str(constants.CONFIDENCE_AUTO_APPLY)
    assert shown["MAX_FILE_MB"] == str(constants.MAX_FILE_MB)


def test_values_read_the_way_a_person_reads_them():
    shown = {i.name: i.value for s in settings_view.build().sections for i in s.items}
    assert shown["TDS_RATE"] == "10%"
    assert shown["REMINDER_SCAN_TIME_IST"] == "09:00 IST"
    assert shown["REMINDER_TIERS"] == "1, 15, 30"
    assert shown["HOLIDAYS_FIXED"] == "01-26, 08-15, 10-02, 12-25"
    assert shown["LAYER_BASE_SCORE"] == "L1 60, L2 45, L3 35"
    assert shown["FX_BAND_PCT"] == "1.5"


def test_render_handles_each_kind_of_value():
    assert settings_view.render(time(9, 15)) == "09:15 IST"
    assert settings_view.render(Decimal("1.00")) == "1"
    assert settings_view.render((3, 9, 15)) == "3, 9, 15"
    assert settings_view.render(frozenset({"B", "A"})) == "A, B"
    assert settings_view.render("FTM") == "FTM"


def test_it_says_it_is_read_only_and_never_shows_secrets():
    out = settings_view.build()
    assert out.editable is False and "code-managed" in out.note
    text = " ".join(f"{i.name} {i.value}" for s in out.sections for i in s.items).lower()
    for forbidden in ("secret", "password", "token_url", "client"):
        assert forbidden not in text
