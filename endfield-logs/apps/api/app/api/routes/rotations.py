import logging

from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.routes.auth import _resolve_session_token
from app.core.config import get_settings
from app.core.errors import AppError
from app.schemas.rotation import (
    RotationCatalog, RotationCreateRequest, RotationDeleteResponse, RotationEnvelope,
    RotationOrderRequest, RotationPlan, RotationUpdateRequest,
)
from app.services.auth import auth_service
from app.services.rotations import rotation_catalog, rotation_service


logger = logging.getLogger(__name__)


class RotationPrivacyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        is_rotation = request.url.path == "/api/rotations" or request.url.path.startswith("/api/rotations/")
        is_sync_list = request.method == "GET" and request.url.path == "/api/rotations"
        if is_rotation and not get_settings().rotations_enabled:
            # Legacy sync clears its active guide on 403; offline errors or an
            # empty plan list can leave an already active legacy guide running.
            response = JSONResponse(status_code=403 if is_sync_list else 410, content={"error": {
                "code": "ROTATIONS_DISABLED", "message": "排轴功能已下线。",
            }})
        else:
            response = await call_next(request)
        if is_rotation:
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
            response.headers["Vary"] = "Cookie, Authorization"
        return response


class RotationRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request: Request):
            try:
                return await original(request)
            except RequestValidationError:
                # Do not echo private notes or request bodies in error details.
                return JSONResponse(status_code=422, content={"error": {
                    "code": "rotation_payload_invalid",
                    "message": "排轴格式不正确：请检查必填项、字符长度、步骤时间顺序及版本号。",
                }})
            except AppError:
                raise
            except Exception:
                logger.exception("Unexpected rotation API error")
                return JSONResponse(status_code=500, headers={"Cache-Control": "no-store"},
                                    content={"error": {"code": "internal_error", "message": "服务器内部错误。"}})
        return handle


router = APIRouter(prefix="/api/rotations", tags=["rotations"], route_class=RotationRoute)


def _owner_id(request: Request) -> str:
    user = auth_service.get_user_from_session(_resolve_session_token(request))
    if user is None:
        raise AppError(401, "unauthorized", "请先登录后再管理排轴。")
    expected_owner = request.headers.get("X-Endfield-Rotation-Account")
    if expected_owner is not None and expected_owner != user.id:
        raise AppError(409, "ROTATION_ACCOUNT_CHANGED", "登录账号已经变更，请重新打开工作台后再操作。")
    return user.id


@router.get("/catalog", response_model=RotationCatalog, response_model_exclude_none=True)
def get_catalog() -> RotationCatalog:
    return rotation_catalog()


@router.get("", response_model=RotationEnvelope, response_model_exclude_none=True)
def list_rotations(request: Request) -> RotationEnvelope:
    return rotation_service.list(_owner_id(request))


@router.post("", response_model=RotationPlan, status_code=201, response_model_exclude_none=True)
def create_rotation(payload: RotationCreateRequest, request: Request) -> RotationPlan:
    return rotation_service.create(_owner_id(request), payload)


@router.post("/order", response_model=RotationEnvelope, response_model_exclude_none=True)
def reorder_rotations(payload: RotationOrderRequest, request: Request) -> RotationEnvelope:
    return rotation_service.reorder(_owner_id(request), payload.ids)


@router.patch("/{plan_id}", response_model=RotationPlan, response_model_exclude_none=True)
def update_rotation(plan_id: str, payload: RotationUpdateRequest, request: Request) -> RotationPlan:
    return rotation_service.update(_owner_id(request), plan_id, payload)


@router.delete("/{plan_id}", response_model=RotationDeleteResponse)
def delete_rotation(plan_id: str, request: Request) -> RotationDeleteResponse:
    rotation_service.delete(_owner_id(request), plan_id)
    return RotationDeleteResponse()
