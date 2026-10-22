"""The refresh token's cookie: HttpOnly (the page's scripts cannot read it), sent only to /auth, never in a response body."""

from fastapi import Request, Response

from api.middleware.cors import ALLOWED_ORIGINS
from config.settings import get_settings
from constants.constants import REFRESH_COOKIE_NAME, REFRESH_COOKIE_PATH
from core.exceptions.errors import PermissionDeniedError


def set_refresh_cookie(response: Response, token: str, max_age: int) -> None:
    settings = get_settings()
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        token,
        max_age=max_age,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
    )


def clear_refresh_cookie(response: Response) -> None:
    settings = get_settings()
    # The attributes must match the ones it was set with, or the browser keeps the old cookie.
    response.delete_cookie(
        REFRESH_COOKIE_NAME,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
    )


def require_same_origin(request: Request) -> None:
    """A cookie rides along on any request a browser makes, so refuse a state-changing call that a page on another origin
    started. A request with no Origin header (a script, a test) is not a browser page, so it is let through."""
    origin = request.headers.get("origin")
    if origin is not None and origin not in ALLOWED_ORIGINS:
        raise PermissionDeniedError("Request from an unknown origin")
