from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from api.rest.dependencies import get_current_user
from core.services.users.auth import authenticate, create_access_token
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
    description="Exchange email and password for a bearer token (valid 8 hours).",
)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    user = await authenticate(db, body.email, body.password)
    token, expires_in = create_access_token(user.id, user.role.code)
    return TokenResponse(access_token=token, expires_in=expires_in)


@router.post(
    "/logout",
    response_model=MessageResponse,
    summary="Log out",
    description=(
        "Tokens are stateless, so the client must discard its token; the server keeps "
        "no session. The token itself stays valid until it expires."
    ),
)
async def logout(_: User = Depends(get_current_user)) -> MessageResponse:
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
