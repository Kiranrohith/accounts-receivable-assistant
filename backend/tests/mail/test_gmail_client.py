import asyncio
import base64
import json

import httpx
import pytest

from core.services.mail import gmail
from core.services.mail.gmail import GmailAuthError, GmailClient, GmailError


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch):
    monkeypatch.setattr(gmail, "retry_delays_seconds", (0, 0, 0))


def client_for(handler) -> GmailClient:
    return GmailClient("id", "secret", "refresh", http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def token_ok(request):
    return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})


def is_token_call(request) -> bool:
    return request.url.host == "oauth2.googleapis.com"


def test_the_access_token_is_fetched_once_and_reused():
    calls = {"token": 0, "api": 0}

    def handler(request):
        if is_token_call(request):
            calls["token"] += 1
            body = dict(x.split("=") for x in request.content.decode().split("&"))
            assert body["grant_type"] == "refresh_token" and body["refresh_token"] == "refresh"
            return token_ok(request)
        calls["api"] += 1
        assert request.headers["Authorization"] == "Bearer tok"
        return httpx.Response(200, json={"emailAddress": "ar@gmail.com"})

    async def run():
        c = client_for(handler)
        await c.profile()
        await c.profile()

    asyncio.run(run())
    assert calls == {"token": 1, "api": 2}


def test_a_dead_refresh_token_says_how_to_fix_it():
    def handler(request):
        return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Token has been expired or revoked."})

    with pytest.raises(GmailAuthError) as err:
        asyncio.run(client_for(handler).profile())
    assert "gmail_authorize.py" in err.value.message and "7 days" in err.value.message


def test_a_401_renews_the_token_once_and_retries():
    seen = {"tokens": 0, "api": 0}

    def handler(request):
        if is_token_call(request):
            seen["tokens"] += 1
            return httpx.Response(200, json={"access_token": f"tok{seen['tokens']}", "expires_in": 3600})
        seen["api"] += 1
        if request.headers["Authorization"] == "Bearer tok1":
            return httpx.Response(401, json={"error": "expired"})
        return httpx.Response(200, json={"ok": True})

    assert asyncio.run(client_for(handler)._call("GET", "/profile")) == {"ok": True}
    assert seen == {"tokens": 2, "api": 2}


def test_a_server_error_is_retried_then_raised():
    calls = []

    def handler(request):
        if is_token_call(request):
            return token_ok(request)
        calls.append(1)
        return httpx.Response(503, text="try later")

    with pytest.raises(GmailError):
        asyncio.run(client_for(handler).profile())
    assert len(calls) == 4  # the first try plus three retries


def test_a_server_error_that_clears_is_survived():
    calls = []

    def handler(request):
        if is_token_call(request):
            return token_ok(request)
        calls.append(1)
        return httpx.Response(500) if len(calls) < 3 else httpx.Response(200, json={"emailAddress": "x"})

    assert asyncio.run(client_for(handler).profile()) == {"emailAddress": "x"}


def test_listing_follows_pages_up_to_the_limit():
    pages = {
        None: {"messages": [{"id": "a"}, {"id": "b"}], "nextPageToken": "p2"},
        "p2": {"messages": [{"id": "c"}, {"id": "d"}]},
    }

    def handler(request):
        if is_token_call(request):
            return token_ok(request)
        return httpx.Response(200, json=pages[request.url.params.get("pageToken")])

    assert asyncio.run(client_for(handler).list_message_ids("in:inbox", 3)) == ["a", "b", "c"]
    assert asyncio.run(client_for(handler).list_message_ids("in:inbox", 10)) == ["a", "b", "c", "d"]


def test_a_raw_message_is_decoded():
    raw = b"From: a@b.c\r\nSubject: hi\r\n\r\nbody"

    def handler(request):
        if is_token_call(request):
            return token_ok(request)
        assert request.url.params["format"] == "raw"
        return httpx.Response(
            200,
            json={
                "id": "m1", "threadId": "t1", "historyId": "9", "internalDate": "1760000000000", "labelIds": ["INBOX"],
                "raw": base64.urlsafe_b64encode(raw).decode().rstrip("="),
            },
        )

    m = asyncio.run(client_for(handler).get_raw("m1"))
    assert (m.gmail_message_id, m.thread_id, m.history_id, m.received_ms, m.label_ids, m.raw) == (
        "m1", "t1", "9", 1760000000000, ("INBOX",), raw,
    )


def test_only_the_missing_labels_are_created():
    created = []

    def handler(request):
        if is_token_call(request):
            return token_ok(request)
        if request.method == "GET":
            return httpx.Response(200, json={"labels": [{"id": "L1", "name": "AR/Ignored"}, {"id": "INBOX", "name": "INBOX"}]})
        body = json.loads(request.content)
        created.append(body["name"])
        return httpx.Response(200, json={"id": f"new-{len(created)}", "name": body["name"]})

    ids = asyncio.run(client_for(handler).ensure_labels(("AR/Ignored", "AR/Triage", "AR/Processed-PO")))
    assert created == ["AR/Triage", "AR/Processed-PO"]
    assert ids == {"AR/Ignored": "L1", "AR/Triage": "new-1", "AR/Processed-PO": "new-2"}


def test_a_label_created_by_someone_else_a_moment_ago_is_found():
    state = {"get": 0}

    def handler(request):
        if is_token_call(request):
            return token_ok(request)
        if request.method == "GET":
            state["get"] += 1
            labels = [] if state["get"] == 1 else [{"id": "L9", "name": "AR/Triage"}]
            return httpx.Response(200, json={"labels": labels})
        return httpx.Response(409, json={"error": "Label name exists or conflicts"})

    assert asyncio.run(client_for(handler).ensure_labels(("AR/Triage",))) == {"AR/Triage": "L9"}


def test_adding_a_label_sends_only_the_label():
    sent = {}

    def handler(request):
        if is_token_call(request):
            return token_ok(request)
        sent["path"], sent["body"] = request.url.path, json.loads(request.content)
        return httpx.Response(200, json={})

    asyncio.run(client_for(handler).add_labels("m1", ["L1"]))
    assert sent["path"].endswith("/messages/m1/modify") and sent["body"] == {"addLabelIds": ["L1"]}


def test_remove_labels_posts_the_remove_list():
    sent = {}

    def handler(request):
        if is_token_call(request):
            return token_ok(request)
        sent["path"], sent["body"] = request.url.path, json.loads(request.content)
        return httpx.Response(200, json={})

    asyncio.run(client_for(handler).remove_labels("m1", ["L1"]))
    assert sent["path"].endswith("/messages/m1/modify") and sent["body"] == {"removeLabelIds": ["L1"]}
