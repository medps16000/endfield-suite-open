from fastapi import APIRouter, Query, Request

from app.api.routes.auth import _resolve_session_token
from app.core.errors import AppError
from app.schemas.binding_code import BINDING_CODE_PATTERN, BindingCodeAccountResponse, BindingCodeResponse
from app.services.auth import auth_service
from app.services.binding_codes import binding_code_service


router = APIRouter(tags=["binding-code"])


@router.post("/api/auth/binding-code", response_model=BindingCodeResponse)
def generate_binding_code(request: Request) -> BindingCodeResponse:
    user = auth_service.get_user_from_session(_resolve_session_token(request))
    if user is None:
        raise AppError(401, "unauthorized", "请先登录后再生成绑定码。")
    return binding_code_service.generate(user.id)


@router.get("/api/battles/users/binding-code", response_model=BindingCodeAccountResponse)
def lookup_binding_code(code: str = Query(min_length=13, max_length=13, pattern=BINDING_CODE_PATTERN)) -> BindingCodeAccountResponse:
    return binding_code_service.lookup(code)
