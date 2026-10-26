from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings

settings = get_settings()

engine_kwargs: dict = {"future": True}
if settings.database_url.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(settings.database_url, **engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def initialize_database() -> None:
    from app.db.models import Base

    Base.metadata.create_all(bind=engine)
    _run_schema_updates()


def _run_schema_updates() -> None:
    inspector = inspect(engine)
    if inspector.has_table("auth_users"):
        existing_columns = {column["name"] for column in inspector.get_columns("auth_users")}
        statements: list[str] = []
        dialect = engine.dialect.name
        if dialect == "postgresql":
            false_literal = "FALSE"
        else:
            false_literal = "0"

        if "is_admin" not in existing_columns:
            statements.append(
                f"ALTER TABLE auth_users ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT {false_literal}"
            )
        if "is_disabled" not in existing_columns:
            statements.append(
                f"ALTER TABLE auth_users ADD COLUMN is_disabled BOOLEAN NOT NULL DEFAULT {false_literal}"
            )

        with engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))

    _upgrade_auth_verification_code_length(inspector)
    _upgrade_uploaded_battle_timer_columns(inspector)
    _ensure_uploaded_battle_indexes(inspector)


def _ensure_uploaded_battle_indexes(inspector) -> None:
    if not inspector.has_table("uploaded_battles"):
        return
    existing_indexes = {index["name"] for index in inspector.get_indexes("uploaded_battles")}
    if "idx_uploaded_battles_ranking" in existing_indexes:
        return
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_uploaded_battles_ranking"
                " ON uploaded_battles (status, clear_flag, boss_slug)"
            )
        )


def _upgrade_uploaded_battle_timer_columns(inspector) -> None:
    if not inspector.has_table("uploaded_battles"):
        return

    existing_columns = {column["name"] for column in inspector.get_columns("uploaded_battles")}
    desired_columns = {
        "time_source": "VARCHAR(64)",
        "timeline_zero_source": "VARCHAR(64)",
        "timer_start_seen": "BOOLEAN",
        "timer_end_seen": "BOOLEAN",
        "official_timer_start_seen": "BOOLEAN",
        "official_timer_end_seen": "BOOLEAN",
        "official_timer_observed_elapsed_ms": "INTEGER",
        "packet_timer_elapsed_ms": "INTEGER",
        "timer_integrity_ok": "BOOLEAN",
        "timer_integrity_reasons_json": "TEXT",
        "timer_evidence_json": "TEXT",
        "timer_start_inferred": "BOOLEAN",
        "timer_window_valid": "BOOLEAN",
        "rdps_preflight_ok": "BOOLEAN",
        "rdps_strict_ok": "BOOLEAN",
        "rdps_preflight_blocker_count": "INTEGER",
        "boss_identity_source": "VARCHAR(64)",
        "dungeon_context_id": "VARCHAR(160)",
        "dungeon_identity_source": "VARCHAR(64)",
        "loadout_fallback_used": "BOOLEAN",
        "contract_tag_score": "INTEGER",
        "contract_tags_json": "TEXT",
        # v33+ 完整施法序列（排轴导出 API）；老 battle 为 NULL
        "casts_json": "TEXT",
    }
    statements = [
        f"ALTER TABLE uploaded_battles ADD COLUMN {column_name} {column_type}"
        for column_name, column_type in desired_columns.items()
        if column_name not in existing_columns
    ]
    if not statements:
        return

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def _upgrade_auth_verification_code_length(inspector) -> None:
    if not inspector.has_table("auth_verification_codes"):
        return
    if engine.dialect.name != "postgresql":
        return

    code_column = next(
        (column for column in inspector.get_columns("auth_verification_codes") if column["name"] == "code"),
        None,
    )
    current_length = getattr(code_column["type"], "length", None) if code_column is not None else None
    if current_length is None or current_length >= 64:
        return

    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE auth_verification_codes ALTER COLUMN code TYPE VARCHAR(64)"))
