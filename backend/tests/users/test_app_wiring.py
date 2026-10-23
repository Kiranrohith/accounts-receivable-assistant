import pytest
from fastapi import Request

from api.rest.users.cookies import require_same_origin
from config.settings import get_settings
from core.exceptions.errors import PermissionDeniedError


def test_the_application_imports_with_every_route_wired():
    # A broken import anywhere in the API (a renamed setting, a moved helper) only shows when the server starts.
    from api.rest.app import app

    paths = app.openapi()["paths"]
    assert len(paths) > 100
    assert "/health" in paths and "/failed-items" in paths and "/mail/live" in paths


def _request(origin: str | None) -> Request:
    headers = [(b"origin", origin.encode())] if origin else []
    return Request({"type": "http", "method": "POST", "path": "/auth/refresh", "headers": headers})


def test_a_call_from_the_configured_frontend_is_allowed_and_one_from_elsewhere_is_not(monkeypatch):
    monkeypatch.setattr(get_settings(), "cors_allowed_origins", "https://ar.example.com,https://admin.example.com")
    require_same_origin(_request("https://ar.example.com"))
    require_same_origin(_request("https://admin.example.com"))
    require_same_origin(_request(None))  # not a browser page: a script or a test
    with pytest.raises(PermissionDeniedError):
        require_same_origin(_request("https://evil.example"))
