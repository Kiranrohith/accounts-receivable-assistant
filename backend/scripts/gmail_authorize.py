"""One-time Gmail authorization: opens Google's consent page, catches the redirect on localhost:8080 and
saves the refresh token into backend/.env as GMAIL_REFRESH_TOKEN (it is never printed).

    uv run python scripts/gmail_authorize.py            # sign in as the AR mailbox in the browser
    uv run python scripts/gmail_authorize.py --print-url  # only show the consent URL (for checking the client)

While the Google app is in Testing status the token expires after 7 days; run this again then.
"""

import json
import re
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from config.settings import get_settings  # noqa: E402

PORT = 8080
REDIRECT_URI = f"http://localhost:{PORT}/"
SCOPE = "https://www.googleapis.com/auth/gmail.modify"
ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


def consent_url(client_id: str, state: str) -> str:
    query = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": SCOPE,
            "access_type": "offline",  # asks for a refresh token
            "prompt": "consent",  # and gets a fresh one every time
            "state": state,
        }
    )
    return f"https://accounts.google.com/o/oauth2/v2/auth?{query}"


def wait_for_code(state: str) -> str:
    result: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            params = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            if "code" in params and params.get("state") == state:
                result["code"] = params["code"]
                message = "Authorization received. You can close this tab and return to the terminal."
            else:
                result["error"] = params.get("error", "no code in the redirect")
                message = f"Authorization failed: {result['error']}"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(message.encode())

        def log_message(self, *args) -> None:  # noqa: ANN002
            pass

    with HTTPServer(("localhost", PORT), Handler) as server:
        server.handle_request()
    if "code" not in result:
        sys.exit(f"Authorization failed: {result.get('error')}")
    return result["code"]


def post(url: str, data: dict[str, str]) -> dict:
    request = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode())
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        sys.exit(f"Google refused the request: {exc.read().decode()[:300]}")


def save_token(token: str) -> None:
    text = ENV_FILE.read_text(encoding="utf-8") if ENV_FILE.exists() else ""
    line = f"GMAIL_REFRESH_TOKEN={token}"
    if re.search(r"^GMAIL_REFRESH_TOKEN\s*=.*$", text, flags=re.M):
        text = re.sub(r"^GMAIL_REFRESH_TOKEN\s*=.*$", lambda _: line, text, flags=re.M)
    else:
        text = text.rstrip("\n") + f"\n{line}\n"
    ENV_FILE.write_text(text, encoding="utf-8")


def main() -> None:
    settings = get_settings()
    if not (settings.gmail_client_id and settings.gmail_client_secret):
        sys.exit("GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET must be set in backend/.env")
    state = secrets.token_urlsafe(16)
    url = consent_url(settings.gmail_client_id.strip(), state)
    if "--print-url" in sys.argv:
        print(url)
        return
    print(f"Opening the consent page. If nothing opens, visit:\n{url}\n\nWaiting on {REDIRECT_URI} ...")
    webbrowser.open(url)
    code = wait_for_code(state)
    tokens = post(
        "https://oauth2.googleapis.com/token",
        {
            "code": code,
            "client_id": settings.gmail_client_id.strip(),
            "client_secret": settings.gmail_client_secret.strip(),
            "redirect_uri": REDIRECT_URI,
            "grant_type": "authorization_code",
        },
    )
    if "refresh_token" not in tokens:
        sys.exit("Google returned no refresh token; remove the app under your Google account's third-party access and retry")
    request = urllib.request.Request(
        "https://gmail.googleapis.com/gmail/v1/users/me/profile",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        profile = json.load(response)
    save_token(tokens["refresh_token"])
    print(f"Saved GMAIL_REFRESH_TOKEN to {ENV_FILE.name} for the mailbox {profile['emailAddress']} "
          f"({profile['messagesTotal']} messages).")


if __name__ == "__main__":
    main()
