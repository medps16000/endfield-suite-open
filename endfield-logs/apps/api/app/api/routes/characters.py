import hashlib
import json
from typing import Literal

from fastapi import APIRouter, Query, Request, Response

from app.schemas.public import CharacterBossStatisticsResponse
from app.services.public_data import public_data_service
from app.schemas.character_profile import CharacterProfileResponse
from app.services.character_profile import character_profile_service

router = APIRouter(prefix="/api/characters", tags=["characters"])


@router.get("/{character_key}/profile", response_model=CharacterProfileResponse)
def get_character_profile(
    character_key: str,
    time_range: Literal["7d", "14d", "30d", "all"] = Query(default="all", alias="range"),
    boss: str | None = Query(default=None, max_length=128),
    details: bool = Query(default=True),
) -> CharacterProfileResponse:
    result = character_profile_service.get_profile(character_key, time_range, boss)
    if details:
        return result
    return result.model_copy(update={"bosses": [
        entry.model_copy(update={"rows": [], "characterRows": []}) for entry in result.bosses
    ]})


@router.get(
    "/{character_key}/boss-statistics",
    response_model=CharacterBossStatisticsResponse,
)
def get_character_boss_statistics(
    character_key: str,
    request: Request,
    metric: Literal["dps", "rdps"] = Query(default="dps"),
    time_range: Literal["7d", "14d", "30d", "all"] = Query(
        default="all",
        alias="range",
    ),
    potential: Literal["0", "1-5", "all"] = Query(default="all"),
) -> CharacterBossStatisticsResponse:
    payload = public_data_service.get_character_boss_statistics(
        character_key,
        metric,
        time_range,
        potential,
    )
    content = json.dumps(
        payload.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    etag = f'W/"{hashlib.sha256(content).hexdigest()}"'
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Cache-Control": "public, max-age=30",
        "ETag": etag,
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)  # type: ignore[return-value]
    return Response(  # type: ignore[return-value]
        content=content,
        media_type="application/json; charset=utf-8",
        headers=headers,
    )
