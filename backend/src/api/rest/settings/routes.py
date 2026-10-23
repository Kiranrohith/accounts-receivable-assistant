from fastapi import APIRouter, Depends

from api.rest.dependencies import require_permission
from core.services import settings_view
from data.models.postgres import User
from schemas.settings.settings import SettingsOut

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get(
    "",
    response_model=SettingsOut,
    summary="The business constants, read-only",
    description=(
        "Requires users.manage (admin). The thresholds, tolerances, schedules and limits the system runs on, grouped by "
        "section, each with its value and a one-line meaning. They are read from the running code, so this always "
        "matches what the system does. `editable` is false: in this version they are changed in code, not here."
    ),
)
async def read_settings(_: User = Depends(require_permission("users.manage"))) -> SettingsOut:
    return settings_view.build()
