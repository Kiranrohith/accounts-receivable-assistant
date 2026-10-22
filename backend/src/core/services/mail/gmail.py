"""The Gmail API, as much of it as we use: list the inbox, fetch one message whole, make labels, label a message.

Everything goes through one access token that is renewed from the refresh token (held in .env; written by
scripts/gmail_authorize.py). The token lives an hour; a 401 renews it once and retries.

Failure handling follows the retry policy of the other outside services: timeouts, dropped connections, 429
and 5xx are retried (3s / 9s / 15s, jittered +/-30%) and then raised as GmailError. A refresh token that Google
no longer accepts (invalid_grant: while the Google app is in Testing status it dies after 7 days, or the
access was revoked) raises GmailAuthError at once, with the one-line fix; retrying cannot help.
"""

import asyncio
import base64
import logging
import random
import time
from dataclasses import dataclass

import httpx

from config.settings import get_settings
from constants.constants import (
    BACKOFF_JITTER,
    GMAIL_API_URL,
    GMAIL_TIMEOUT_SECONDS,
    GMAIL_TOKEN_URL,
    OTHER_RETRY_DELAYS_SECONDS,
)
from core.exceptions.errors import ExternalServiceError

logger = logging.getLogger(__name__)

# Module-level so tests can shorten them.
retry_delays_seconds: tuple[float, ...] = OTHER_RETRY_DELAYS_SECONDS

RE_AUTHORIZE = "run `uv run python scripts/gmail_authorize.py` to authorize the mailbox again"


class GmailError(ExternalServiceError):
    """Gmail could not be reached or refused a request; worth trying again later."""


class GmailAuthError(ExternalServiceError):
    """The saved Gmail authorization no longer works. Only a person can fix it."""


@dataclass(frozen=True)
class RawMessage:
    gmail_message_id: str
    thread_id: str | None
    history_id: str | None
    received_ms: int | None  # Gmail's internalDate, milliseconds since the epoch
    label_ids: tuple[str, ...]
    raw: bytes  # the whole RFC 822 message: headers, body and attachments


def _jittered(seconds: float) -> float:
    return seconds * (1 + random.uniform(-float(BACKOFF_JITTER), float(BACKOFF_JITTER)))


class GmailClient:
    def __init__(
        self, client_id: str, client_secret: str, refresh_token: str, *, http: httpx.AsyncClient | None = None
    ) -> None:
        self._id, self._secret, self._refresh = client_id.strip(), client_secret.strip(), refresh_token.strip()
        self._http = http or httpx.AsyncClient(timeout=GMAIL_TIMEOUT_SECONDS)
        self._token: str | None = None
        self._token_expires = 0.0

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ authorization
    async def _access_token(self, *, force: bool = False) -> str:
        if self._token and not force and time.monotonic() < self._token_expires - 60:
            return self._token
        response = await self._with_retries(
            lambda: self._http.post(
                GMAIL_TOKEN_URL,
                data={
                    "client_id": self._id,
                    "client_secret": self._secret,
                    "refresh_token": self._refresh,
                    "grant_type": "refresh_token",
                },
            ),
            authorize=False,
        )
        body = response.json() if response.content else {}
        if response.status_code >= 400:
            if body.get("error") in ("invalid_grant", "invalid_client", "unauthorized_client"):
                raise GmailAuthError(
                    f"Google no longer accepts the saved Gmail authorization ({body.get('error')}: "
                    f"{body.get('error_description', '')}). While the Google app is in Testing status the "
                    f"authorization expires after 7 days: {RE_AUTHORIZE}."
                )
            raise GmailError(f"Google refused the token request: {response.status_code} {response.text[:200]}")
        self._token = body["access_token"]
        self._token_expires = time.monotonic() + float(body.get("expires_in", 3600))
        return self._token

    # ------------------------------------------------------------------ plumbing
    async def _with_retries(self, send, *, authorize: bool = True) -> httpx.Response:  # noqa: ANN001
        for delay in (*retry_delays_seconds, None):
            try:
                response = await send()
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                if delay is None:
                    raise GmailError(f"Gmail could not be reached: {exc}") from exc
                await asyncio.sleep(_jittered(delay))
                continue
            if response.status_code == 429 or response.status_code >= 500:
                if delay is None:
                    raise GmailError(f"Gmail answered {response.status_code}: {response.text[:200]}")
                await asyncio.sleep(_jittered(delay))
                continue
            return response
        raise AssertionError("unreachable")  # pragma: no cover

    async def _call(self, method: str, path: str, **kwargs) -> dict:  # noqa: ANN003
        renewed = False
        while True:
            token = await self._access_token(force=renewed)
            response = await self._with_retries(
                lambda: self._http.request(
                    method, f"{GMAIL_API_URL}{path}", headers={"Authorization": f"Bearer {token}"}, **kwargs
                )
            )
            if response.status_code == 401 and not renewed:
                renewed = True  # the hour ran out between the check and the call: renew once
                continue
            if response.status_code in (401, 403) and "insufficient" in response.text.lower():
                raise GmailAuthError(f"The saved Gmail authorization lacks a permission: {RE_AUTHORIZE}.")
            if response.status_code >= 400:
                raise GmailError(f"Gmail answered {response.status_code} for {method} {path}: {response.text[:200]}")
            return response.json() if response.content else {}

    # ------------------------------------------------------------------ what we use
    async def profile(self) -> dict:
        return await self._call("GET", "/profile")

    async def list_message_ids(self, query: str, limit: int) -> list[str]:
        """Ids of messages matching `query`, newest first, at most `limit`."""
        ids: list[str] = []
        page: str | None = None
        while len(ids) < limit:
            params = {"q": query, "maxResults": min(limit - len(ids), 100)}
            if page:
                params["pageToken"] = page
            body = await self._call("GET", "/messages", params=params)
            ids += [m["id"] for m in body.get("messages", [])]
            page = body.get("nextPageToken")
            if not page:
                break
        return ids[:limit]

    async def get_raw(self, message_id: str) -> RawMessage:
        body = await self._call("GET", f"/messages/{message_id}", params={"format": "raw"})
        return RawMessage(
            gmail_message_id=body["id"],
            thread_id=body.get("threadId"),
            history_id=body.get("historyId"),
            received_ms=int(body["internalDate"]) if body.get("internalDate") else None,
            label_ids=tuple(body.get("labelIds", [])),
            raw=base64.urlsafe_b64decode(body["raw"] + "=" * (-len(body["raw"]) % 4)),
        )

    async def ensure_labels(self, names: tuple[str, ...]) -> dict[str, str]:
        """Make sure each label exists; returns name -> Gmail label id. Safe to call at every boot."""
        existing = {x["name"]: x["id"] for x in (await self._call("GET", "/labels")).get("labels", [])}
        for name in names:
            if name in existing:
                continue
            try:
                created = await self._call(
                    "POST", "/labels",
                    json={"name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show"},
                )
                existing[name] = created["id"]
            except GmailError:
                # another process may have created it a moment ago: look again before giving up
                existing = {x["name"]: x["id"] for x in (await self._call("GET", "/labels")).get("labels", [])}
                if name not in existing:
                    raise
        return {name: existing[name] for name in names}

    async def add_labels(self, message_id: str, label_ids: list[str]) -> None:
        await self._call("POST", f"/messages/{message_id}/modify", json={"addLabelIds": label_ids})

    async def remove_labels(self, message_id: str, label_ids: list[str]) -> None:
        await self._call("POST", f"/messages/{message_id}/modify", json={"removeLabelIds": label_ids})


def configured() -> bool:
    s = get_settings()
    return bool(s.gmail_client_id and s.gmail_client_secret and s.gmail_refresh_token)


def enabled() -> bool:
    """Polling is on only when it is not switched off AND the mailbox has been authorized."""
    return get_settings().gmail_poll_enabled and configured()


def get_client() -> GmailClient:
    s = get_settings()
    if not configured():
        raise GmailAuthError(f"Gmail is not authorized yet: {RE_AUTHORIZE}.")
    return GmailClient(s.gmail_client_id, s.gmail_client_secret, s.gmail_refresh_token)
