from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _parse_csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _default_database_url() -> str:
    repo_root = Path(__file__).resolve().parents[4]
    database_dir = repo_root / ".local"
    database_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite+pysqlite:///{(database_dir / 'endfield_logs_dev.db').as_posix()}"


class Settings(BaseSettings):
    app_env: str = "development"
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    database_url: str = _default_database_url()
    cors_origins: str = "http://127.0.0.1:3000,http://localhost:3000"
    allowed_hosts: str = "127.0.0.1,localhost,testserver"
    csrf_trusted_origins: str = ""
    integrity_secret: str = "dev-only-change-me"
    admin_emails: str = ""
    session_cookie_domain: str | None = None
    session_cookie_secure: bool | None = None
    auth_debug_code_enabled: bool | None = None
    expose_api_docs: bool | None = None
    rotations_enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_ssl: bool = True
    smtp_timeout_seconds: int = 10
    mail_from_address: str = ""
    mail_from_name: str = "ZMDLogs"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False)

    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() == "production"

    @property
    def parsed_cors_origins(self) -> tuple[str, ...]:
        return _parse_csv(self.cors_origins)

    @property
    def parsed_allowed_hosts(self) -> tuple[str, ...]:
        return _parse_csv(self.allowed_hosts)

    @property
    def parsed_csrf_trusted_origins(self) -> tuple[str, ...]:
        explicit_origins = _parse_csv(self.csrf_trusted_origins)
        if explicit_origins:
            return explicit_origins
        return self.parsed_cors_origins

    @property
    def session_cookie_secure_enabled(self) -> bool:
        if self.session_cookie_secure is not None:
            return self.session_cookie_secure
        return self.is_production

    @property
    def auth_debug_code_exposed(self) -> bool:
        if self.auth_debug_code_enabled is not None:
            return self.auth_debug_code_enabled
        return not self.is_production

    @property
    def api_docs_enabled(self) -> bool:
        if self.expose_api_docs is not None:
            return self.expose_api_docs
        return not self.is_production


@lru_cache
def get_settings() -> Settings:
    return Settings()
