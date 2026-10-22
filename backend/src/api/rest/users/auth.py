from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api.rest.dependencies import get_current_user
from api.rest.users.cookies import clear_refresh_cookie, require_same_origin, set_refresh_cookie
from constants.constants import REFRESH_COOKIE_NAME
from core.exceptions.errors import AuthenticationError
from core.services.users.auth import authenticate, create_access_token, create_refresh_token, refresh_session
from core.services.users.users import permission_codes_for
from data.clients.db import get_db
from data.models.postgres import User
from schemas.users.auth import LoginRequest, MeResponse, MessageResponse, TokenResponse
from schemas.users.users import UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Log in",
    description=(
        "Exchange email and password for a short-lived bearer token (in the body) and a refresh token "
        "(in an HttpOnly cookie, never in the body)."
    ),
)
async def login(body: LoginRequest, response: Response, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    user = await authenticate(db, body.email, body.password)
    token, expires_in = create_access_token(user.id, user.role.code)
    refresh, refresh_seconds = create_refresh_token(user.id)
    set_refresh_cookie(response, refresh, refresh_seconds)
    return TokenResponse(access_token=token, expires_in=expires_in)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="New bearer token from the refresh cookie",
    description=(
        "Reads the refresh cookie and returns a new bearer token; the cookie is renewed too. 401 (and the cookie is "
        "cleared) when it is missing, expired, not a refresh token, or its user is gone or deactivated."
    ),
    dependencies=[Depends(require_same_origin)],
)
async def refresh(request: Request, response: Response, db: AsyncSession = Depends(get_db)) -> TokenResponse | JSONResponse:
    cookie = request.cookies.get(REFRESH_COOKIE_NAME)
    try:
        if not cookie:
            raise AuthenticationError("Not signed in")
        _, access, expires_in, new_refresh, refresh_seconds = await refresh_session(db, cookie)
    except AuthenticationError as exc:
        # Built here, not raised: the error handler would make a new response and drop the cookie-clearing header.
        failure = JSONResponse(status_code=401, content={"detail": exc.message}, headers={"WWW-Authenticate": "Bearer"})
        clear_refresh_cookie(failure)  # a stale cookie must not keep triggering this
        return failure
    set_refresh_cookie(response, new_refresh, refresh_seconds)
    return TokenResponse(access_token=access, expires_in=expires_in)


@router.post(
    "/logout",
    response_model=MessageResponse,
    summary="Log out",
    description=(
        "Clears the refresh cookie. It needs no bearer token, so it still works after the access token has expired. "
        "Tokens are stateless: one already issued stays valid until it expires."
    ),
    dependencies=[Depends(require_same_origin)],
)
async def logout(response: Response) -> MessageResponse:
    clear_refresh_cookie(response)
    return MessageResponse(message="Logged out; discard your token")


@router.get(
    "/me",
    response_model=MeResponse,
    summary="Current user",
    description="The authenticated user, their role and the permissions that role holds.",
)
async def me(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> MeResponse:
    return MeResponse(
        **UserOut.from_user(user).model_dump(),
        permissions=await permission_codes_for(db, user),
    )
