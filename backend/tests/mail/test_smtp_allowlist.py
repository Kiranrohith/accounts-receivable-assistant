"""Decision 65: the recipient allowlist is checked before any connection, and login + STARTTLS happen only
when credentials are set. No network: the connection itself is replaced by a recorder."""

import asyncio
import smtplib

import pytest

from core.services.mail import smtp


@pytest.fixture
def connections(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(smtp, "_send_once", lambda host, port, msg, *creds: calls.append((host, port, creds)))
    monkeypatch.setattr(smtp, "retry_delays_seconds", (0, 0, 0))
    return calls


def mail(*to: str, cc: str | None = None):
    m = smtp.build_message(to=", ".join(to), subject="s", body="b", sender="ar@genworx.local")
    if cc:
        m["Cc"] = cc
    return m


def restrict(settings, allowed: str):
    settings.smtp_allowlist_bypass = False
    settings.smtp_allowed_recipients = allowed


def test_an_allowlisted_recipient_is_sent(mail_sink, connections):
    restrict(mail_sink, "a@client.in, B@client.in")
    asyncio.run(smtp.send(mail("b@client.in")))
    assert len(connections) == 1


def test_an_unlisted_recipient_is_refused_without_connecting(mail_sink, connections):
    restrict(mail_sink, "a@client.in")
    with pytest.raises(smtp.RecipientNotAllowed) as err:
        asyncio.run(smtp.send(mail("stranger@elsewhere.com")))
    assert not connections
    assert err.value.permanent and "recipient not in the demo allowlist" in err.value.message


def test_one_unlisted_recipient_among_several_refuses_the_whole_mail(mail_sink, connections):
    restrict(mail_sink, "a@client.in, b@client.in")
    with pytest.raises(smtp.RecipientNotAllowed) as err:
        asyncio.run(smtp.send(mail("a@client.in", cc="Boss <boss@elsewhere.com>")))
    assert not connections and "boss@elsewhere.com" in err.value.message


def test_a_display_name_is_ignored_when_checking(mail_sink, connections):
    restrict(mail_sink, "a@client.in")
    asyncio.run(smtp.send(mail("Anita Rao <A@Client.in>")))
    assert len(connections) == 1


def test_an_empty_allowlist_refuses_everything(mail_sink, connections):
    restrict(mail_sink, "")
    with pytest.raises(smtp.RecipientNotAllowed):
        asyncio.run(smtp.send(mail("a@client.in")))
    assert not connections


def test_the_bypass_flag_lets_an_empty_allowlist_send(mail_sink, connections):
    mail_sink.smtp_allowlist_bypass = True
    mail_sink.smtp_allowed_recipients = ""
    asyncio.run(smtp.send(mail("anyone@test.invalid")))
    assert len(connections) == 1


def test_the_credentials_are_handed_to_the_connection(mail_sink, connections):
    mail_sink.smtp_user, mail_sink.smtp_password = "ar@gmail.com", "app-password"
    asyncio.run(smtp.send(mail("a@client.in")))
    assert connections[0][2] == ("ar@gmail.com", "app-password")


class FakeServer:
    log: list[str] = []

    def __init__(self, host, port, timeout=None):
        self.log.append(f"connect {host}:{port}")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        self.log.append("starttls")

    def login(self, user, password):
        self.log.append(f"login {user}")

    def send_message(self, message):
        self.log.append("send")


def test_starttls_and_login_happen_only_with_credentials(monkeypatch):
    monkeypatch.setattr(smtplib, "SMTP", FakeServer)
    FakeServer.log = []
    smtp._send_once("smtp.gmail.com", 587, mail("a@client.in"), "ar@gmail.com", "pw")
    assert FakeServer.log == ["connect smtp.gmail.com:587", "starttls", "login ar@gmail.com", "send"]
    FakeServer.log = []
    smtp._send_once("localhost", 1025, mail("a@client.in"))
    assert FakeServer.log == ["connect localhost:1025", "send"]
