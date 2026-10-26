from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class UploadedBattleRecord(Base):
    __tablename__ = "uploaded_battles"
    __table_args__ = (
        Index("idx_uploaded_battles_ranking", "status", "clear_flag", "boss_slug"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    uploader_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    uploader_nickname: Mapped[str] = mapped_column(String(128), nullable=False)

    boss_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    boss_slug: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    boss_name: Mapped[str] = mapped_column(String(255), nullable=False)
    dungeon_name: Mapped[str] = mapped_column(String(255), nullable=False)

    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    battle_start_at: Mapped[str] = mapped_column(String(64), nullable=False)
    battle_end_at: Mapped[str] = mapped_column(String(64), nullable=False)
    total_damage: Mapped[int] = mapped_column(Integer, nullable=False)
    total_dps: Mapped[float] = mapped_column(Float, nullable=False)
    clear_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    roster_json: Mapped[str] = mapped_column(Text, nullable=False)
    participants_json: Mapped[str] = mapped_column(Text, nullable=False)
    timeline_events_json: Mapped[str] = mapped_column(Text, nullable=False)
    role_skill_stats_json: Mapped[str] = mapped_column(Text, nullable=False)
    # v33+：完整施法序列（排轴导出 API）；老 battle 为 NULL = 不支持导出
    casts_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    rules_version: Mapped[str] = mapped_column(String(64), nullable=False)
    battle_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    time_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    timeline_zero_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    timer_start_seen: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    timer_end_seen: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    official_timer_start_seen: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    official_timer_end_seen: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    official_timer_observed_elapsed_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    packet_timer_elapsed_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timer_integrity_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    timer_integrity_reasons_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    timer_evidence_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    timer_start_inferred: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    timer_window_valid: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    rdps_preflight_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    rdps_strict_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    rdps_preflight_blocker_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    boss_identity_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dungeon_context_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    dungeon_identity_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    loadout_fallback_used: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    contract_tag_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    contract_tags_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    visibility: Mapped[str] = mapped_column(String(16), nullable=False, default="public")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="valid", index=True)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)


class AuthUserRecord(Base):
    __tablename__ = "auth_users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    nickname: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    password_salt: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class AuthBindingCodeRecord(Base):
    __tablename__ = "auth_binding_codes"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    code_digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    recent_generations_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")


class AuthVerificationCodeRecord(Base):
    __tablename__ = "auth_verification_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuthPendingProfileRecord(Base):
    __tablename__ = "auth_pending_profiles"

    token: Mapped[str] = mapped_column(String(128), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuthSessionRecord(Base):
    __tablename__ = "auth_sessions"

    token: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    nickname: Mapped[str] = mapped_column(String(128), nullable=False)
    client_type: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class RotationPlanRecord(Base):
    __tablename__ = "rotation_plans"
    __table_args__ = (Index("idx_rotation_plans_owner_order", "owner_user_id", "position"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    dungeon_id: Mapped[str] = mapped_column(String(160), nullable=False)
    character_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    steps_json: Mapped[str] = mapped_column(Text, nullable=False)
    display_notes: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)
