"""Access and refresh tokens: each opens only its own door, expiry and tampering are refused, and a token issued before
refresh existed (no type claim) keeps working as an access token."""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from config.settings import get_settings
from constants.constants import ACCESS_TOKEN_MINUTES, REFRESH_TOKEN_DAYS
from core.exceptions.errors import AuthenticationError
from core.services.users import auth

USER = uuid.UUID("11111111-1111-1111-1111-111111111111")


def _forge(**claims):
    now = datetime.now(UTC)
    body = {"sub": str(USER), "iat": now, "exp": now + timedelta(minutes=5), **claims}
    return jwt.encode(body, get_settings().jwt_secret, algorithm="HS256")


def test_access_token_carries_role_and_lives_minutes():
    token, seconds = auth.create_access_token(USER, "ADM")
    claims = auth.decode_token(token)
    assert claims["sub"] == str(USER) and claims["role"] == "ADM" and claims["typ"] == "access"
    assert seconds == ACCESS_TOKEN_MINUTES * 60


def test_refresh_token_carries_no_role_and_lives_days():
    token, seconds = auth.create_refresh_token(USER)
    claims = auth.decode_token(token, auth.REFRESH)
    assert claims["typ"] == "refresh" and "role" not in claims
    assert seconds == REFRESH_TOKEN_DAYS * 86400


def test_a_refresh_token_cannot_open_an_api_call():
    refresh, _ = auth.create_refresh_token(USER)
    with pytest.raises(AuthenticationError):
        auth.decode_token(refresh)  # the default is "access"


def test_an_access_token_cannot_mint_a_new_one():
    access, _ = auth.create_access_token(USER, "FA")
    with pytest.raises(AuthenticationError):
        auth.decode_token(access, auth.REFRESH)


def test_a_token_from_before_refresh_existed_is_an_access_token_only():
    legacy = _forge(role="FA")  # no "typ"
    assert auth.decode_token(legacy)["role"] == "FA"
    with pytest.raises(AuthenticationError):
        auth.decode_token(legacy, auth.REFRESH)


def test_expired_and_tampered_tokens_are_refused():
    expired = _forge(typ="access", exp=datetime.now(UTC) - timedelta(seconds=1))
    with pytest.raises(AuthenticationError):
        auth.decode_token(expired)
    token, _ = auth.create_access_token(USER, "FA")
    head, body, sig = token.split(".")
    with pytest.raises(AuthenticationError):
        auth.decode_token(f"{head}.{body}.{sig[:-2]}xx")
    wrong_key = jwt.encode({"sub": str(USER), "typ": "access", "exp": datetime.now(UTC) + timedelta(minutes=5)}, "another-secret-key-of-length-32-bytes!", algorithm="HS256")
    with pytest.raises(AuthenticationError):
        auth.decode_token(wrong_key)
