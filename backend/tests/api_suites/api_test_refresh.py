"""Refresh tokens: the refresh token lives only in an HttpOnly cookie scoped to /auth, an access token and a refresh token
open different doors, a deactivated user cannot refresh, and logout works without a bearer token."""

import asyncio
import sys
from datetime import UTC, datetime, timedelta

import httpx
import jwt
from sqlalchemy import text

sys.path.insert(0, "src")
from api.rest.app import app
from config.settings import get_settings
from data.clients.db import SessionLocal, engine

s = get_settings()
assert "ar_refresh" in s.database_url, "refusing to run against any database but its own"
results = []


def check(name, cond, extra=""):
    results.append(cond)
    print(f"RESULT {'PASS' if cond else 'FAIL'} {name} {extra}")


def client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def set_cookie_of(r) -> str:
    return r.headers.get("set-cookie", "")


def claims_of(token: str) -> dict:
    return jwt.decode(token, options={"verify_signature": False})


def forge(**over):
    now = datetime.now(UTC)
    body = {"sub": over.pop("sub"), "iat": now, "exp": now + timedelta(minutes=5), **over}
    return jwt.encode(body, s.jwt_secret, algorithm="HS256")


async def main():
    async with client() as c:
        # --- login sets the cookie, and only the cookie
        bad = await c.post("/auth/login", json={"email": s.seed_admin_email, "password": "wrong-password"})
        check("a wrong password sets no cookie", bad.status_code == 401 and "refresh_token" not in set_cookie_of(bad))

        r = await c.post("/auth/login", json={"email": s.seed_admin_email, "password": s.seed_admin_password})
        body = r.json()
        cookie = set_cookie_of(r)
        check("login -> 200 with a bearer token in the body", r.status_code == 200 and body["token_type"] == "bearer")
        check("the body has no refresh token", set(body) == {"access_token", "token_type", "expires_in"})
        check("access token lives 30 minutes", body["expires_in"] == 30 * 60 and claims_of(body["access_token"])["typ"] == "access")
        check("cookie is HttpOnly, Path=/auth, SameSite=lax, 7 days",
              "refresh_token=" in cookie and "HttpOnly" in cookie and "Path=/auth" in cookie
              and "SameSite=lax" in cookie and f"Max-Age={7 * 86400}" in cookie, cookie[:60])
        refresh_value = c.cookies.get("refresh_token", path="/auth")
        check("the cookie holds a refresh token, with no role", claims_of(refresh_value)["typ"] == "refresh" and "role" not in claims_of(refresh_value))
        ADMIN = {"Authorization": f"Bearer {body['access_token']}"}

        # --- the browser sends it to /auth only
        other = await c.get("/customers", headers=ADMIN)
        check("the cookie is not sent to an API route outside /auth", other.status_code == 200 and "cookie" not in other.request.headers)
        sent = await c.post("/auth/refresh")
        check("the cookie is sent to /auth/refresh", sent.request.headers.get("cookie", "").startswith("refresh_token="))

        # --- refresh
        check("refresh -> 200 with a new bearer token that works", sent.status_code == 200
              and (await c.get("/auth/me", headers={"Authorization": f"Bearer {sent.json()['access_token']}"})).status_code == 200)
        check("refresh renews the cookie", "refresh_token=" in set_cookie_of(sent) and f"Max-Age={7 * 86400}" in set_cookie_of(sent))

        # --- the two kinds of token open different doors
        r = await c.get("/auth/me", headers={"Authorization": f"Bearer {refresh_value}"})
        check("a refresh token is refused as a bearer token -> 401", r.status_code == 401)
        r = await c.get("/customers", headers={"Authorization": f"Bearer {refresh_value}"})
        check("and on any other route -> 401", r.status_code == 401)

    async with client() as c:
        r = await c.post("/auth/refresh")
        check("refresh without a cookie -> 401", r.status_code == 401 and r.json()["detail"] == "Not signed in")
        check("and the stale cookie is cleared", "refresh_token=" in set_cookie_of(r) and "Max-Age=0" in set_cookie_of(r))

        access_as_cookie = forge(sub="00000000-0000-0000-0000-000000000000", typ="access", role="ADM")
        r = await c.post("/auth/refresh", cookies={"refresh_token": access_as_cookie})
        check("an access token in the cookie is refused -> 401", r.status_code == 401)

        r = await c.post("/auth/refresh", cookies={"refresh_token": "abc.def.ghi"})
        check("a tampered cookie -> 401", r.status_code == 401)

        expired = forge(sub="00000000-0000-0000-0000-000000000000", typ="refresh", exp=datetime.now(UTC) - timedelta(seconds=5))
        r = await c.post("/auth/refresh", cookies={"refresh_token": expired})
        check("an expired refresh token -> 401", r.status_code == 401)

        unknown = forge(sub="00000000-0000-0000-0000-000000000000", typ="refresh")
        r = await c.post("/auth/refresh", cookies={"refresh_token": unknown})
        check("a refresh token for a user that does not exist -> 401", r.status_code == 401)

    # --- a token issued before refresh existed has no type: still an access token, never a refresh token
    async with client() as c:
        me = (await c.post("/auth/login", json={"email": s.seed_admin_email, "password": s.seed_admin_password})).json()
        admin_id = claims_of(me["access_token"])["sub"]
        legacy = forge(sub=admin_id, role="ADM")
        check("a token with no type still works as an access token", (await c.get("/auth/me", headers={"Authorization": f"Bearer {legacy}"})).status_code == 200)
        check("but cannot be refreshed with", (await c.post("/auth/refresh", cookies={"refresh_token": legacy})).status_code == 401)

    # --- a role change and a deactivation take effect at the next refresh
    async with client() as c:
        ADMIN = {"Authorization": f"Bearer {(await c.post('/auth/login', json={'email': s.seed_admin_email, 'password': s.seed_admin_password})).json()['access_token']}"}
        made = await c.post("/admin/users", headers=ADMIN, json={"name": "Fa Refresh", "email": "fa.refresh@example.com", "password": "farefresh1", "role": "FA"})
        check("created a test user", made.status_code == 201)

    async with client() as u:
        r = await u.post("/auth/login", json={"email": "fa.refresh@example.com", "password": "farefresh1"})
        check("test user's token says FA", claims_of(r.json()["access_token"])["role"] == "FA")
        async with SessionLocal() as db:
            await db.execute(text("update users set role_id=(select id from roles where code='FTM') where email='fa.refresh@example.com'"))
            await db.commit()
        r = await u.post("/auth/refresh")
        check("refresh reads the role again: now FTM", r.status_code == 200 and claims_of(r.json()["access_token"])["role"] == "FTM")

        async with SessionLocal() as db:
            await db.execute(text("update users set is_active=false where email='fa.refresh@example.com'"))
            await db.commit()
        r = await u.post("/auth/refresh")
        check("a deactivated user cannot refresh -> 401", r.status_code == 401 and "Max-Age=0" in set_cookie_of(r))

    # --- same-origin check and CORS
    async with client() as c:
        await c.post("/auth/login", json={"email": s.seed_admin_email, "password": s.seed_admin_password})
        r = await c.post("/auth/refresh", headers={"Origin": "http://evil.example"})
        check("refresh from an unknown origin -> 403", r.status_code == 403)
        r = await c.post("/auth/logout", headers={"Origin": "http://evil.example"})
        check("logout from an unknown origin -> 403", r.status_code == 403)
        r = await c.post("/auth/refresh", headers={"Origin": "http://localhost:5173"})
        check("refresh from the app's own origin -> 200", r.status_code == 200)
        pre = await c.options("/auth/refresh", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"})
        check("CORS allows credentials for the app origin only",
              pre.headers.get("access-control-allow-credentials") == "true" and pre.headers.get("access-control-allow-origin") == "http://localhost:5173")
        pre = await c.options("/auth/refresh", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"})
        check("and not for another origin", pre.headers.get("access-control-allow-origin") is None)

    # --- logout
    async with client() as c:
        login = (await c.post("/auth/login", json={"email": s.seed_admin_email, "password": s.seed_admin_password})).json()
        r = await c.post("/auth/logout")
        check("logout needs no bearer token -> 200", r.status_code == 200)
        check("and clears the cookie", "refresh_token=" in set_cookie_of(r) and "Max-Age=0" in set_cookie_of(r) and "Path=/auth" in set_cookie_of(r))
        check("after logout the browser can no longer refresh -> 401", (await c.post("/auth/refresh")).status_code == 401)
        check("an access token already issued stays valid until it expires (stateless)",
              (await c.get("/auth/me", headers={"Authorization": f"Bearer {login['access_token']}"})).status_code == 200)

    print(f"RESULT {sum(results)}/{len(results)} passed")
    await engine.dispose()


asyncio.run(main())
