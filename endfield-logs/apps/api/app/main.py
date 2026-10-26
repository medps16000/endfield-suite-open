from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from app.core.config import get_settings
from app.core.binding_code_security import BindingCodeProtectionMiddleware, install_binding_code_log_filter
from app.core.errors import install_exception_handlers
from app.core.security import CookieCsrfMiddleware, SecurityHeadersMiddleware
from app.db.session import initialize_database
from app.services.auth import SESSION_COOKIE_NAME


def create_app() -> FastAPI:
    settings = get_settings()
    # Schema upgrades must run before importing route modules: several routes
    # instantiate PublicDataService at import time and immediately query every
    # mapped UploadedBattleRecord column.
    initialize_database()
    install_binding_code_log_filter()
    from app.api.routes.admin import router as admin_router
    from app.api.routes.auth import router as auth_router
    from app.api.routes.battles import router as battles_router
    from app.api.routes.binding_codes import router as binding_codes_router
    from app.api.routes.bosses import router as bosses_router
    from app.api.routes.characters import router as characters_router
    from app.api.routes.game_data import router as game_data_router
    from app.api.routes.game_semantic_hints import router as game_semantic_hints_router
    from app.api.routes.game_semantics import router as game_semantics_router
    from app.api.routes.health import router as health_router
    from app.api.routes.home import router as home_router
    from app.api.routes.integrity import router as integrity_router
    from app.api.routes.public_export import router as public_export_router
    from app.api.routes.rotations import RotationPrivacyMiddleware, router as rotations_router
    from app.api.routes.uploader_battles import router as uploader_battles_router

    app = FastAPI(
        title="Endfield Logs API",
        version="0.1.0",
        debug=not settings.is_production,
        docs_url="/docs" if settings.api_docs_enabled else None,
        redoc_url="/redoc" if settings.api_docs_enabled else None,
        openapi_url="/openapi.json" if settings.api_docs_enabled else None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.parsed_cors_origins),
        allow_credentials=True,
        allow_methods=["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST"],
        allow_headers=["Accept", "Authorization", "Content-Type", "Origin", "X-Endfield-Rotation-Account"],
    )
    app.add_middleware(
        CookieCsrfMiddleware,
        session_cookie_name=SESSION_COOKIE_NAME,
        trusted_origins=settings.parsed_csrf_trusted_origins,
        allow_missing_origin=not settings.is_production,
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(BindingCodeProtectionMiddleware)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.parsed_allowed_hosts))
    app.add_middleware(RotationPrivacyMiddleware)
    install_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(binding_codes_router)
    app.include_router(admin_router)
    app.include_router(home_router)
    app.include_router(game_data_router)
    app.include_router(game_semantic_hints_router)
    app.include_router(game_semantics_router)
    app.include_router(integrity_router)
    app.include_router(uploader_battles_router)
    app.include_router(bosses_router)
    app.include_router(characters_router)
    app.include_router(battles_router)
    app.include_router(public_export_router)
    app.include_router(rotations_router)
    return app


app = create_app()
