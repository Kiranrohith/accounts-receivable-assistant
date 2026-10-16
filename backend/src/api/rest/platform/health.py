from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health", summary="Health check", description="Liveness probe; no auth.")
async def health() -> dict[str, str]:
    return {"status": "ok"}
