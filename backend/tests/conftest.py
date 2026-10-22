import pytest

from config.settings import get_settings


@pytest.fixture(autouse=True)
def mail_sink(monkeypatch):
    """Whatever .env holds (it may point at the real Gmail account), a test never logs in anywhere.
    The allowlist tests set their own values on top of this."""
    settings = get_settings()
    monkeypatch.setattr(settings, "smtp_user", None)
    monkeypatch.setattr(settings, "smtp_password", None)
    monkeypatch.setattr(settings, "smtp_allowlist_bypass", True)
    monkeypatch.setattr(settings, "smtp_allowed_recipients", "")
    monkeypatch.setattr(settings, "gmail_poll_enabled", False)  # a test never touches the real mailbox
    return settings
