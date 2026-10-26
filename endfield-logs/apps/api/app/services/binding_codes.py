from datetime import UTC, datetime, timedelta
from hashlib import sha256
from hmac import compare_digest
import json
from secrets import choice
from threading import Lock

from sqlalchemy import select

from app.core.errors import AppError
from app.db.models import AuthBindingCodeRecord, AuthUserRecord
from app.db.session import SessionLocal
from app.schemas.binding_code import BINDING_CODE_ALPHABET, BindingCodeAccountResponse, BindingCodeResponse


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _digest(code: str) -> str:
    return sha256(f"binding-code-v1:{code}".encode("ascii")).hexdigest()


class BindingCodeService:
    ttl = timedelta(minutes=10)

    def __init__(self) -> None:
        # SQLite ignores FOR UPDATE; this also serializes local SQLite writers.
        self._generation_lock = Lock()

    def generate(self, user_id: str) -> BindingCodeResponse:
        with self._generation_lock, SessionLocal() as session:
            # Lock the user row, which exists even on the first generation.
            user = session.execute(
                select(AuthUserRecord).where(AuthUserRecord.id == user_id).with_for_update()
            ).scalar_one_or_none()
            if user is None or user.is_disabled:
                raise AppError(401, "unauthorized", "请先登录后再生成绑定码。")
            now = _utcnow()
            record = session.get(AuthBindingCodeRecord, user_id)
            recent = json.loads(record.recent_generations_json) if record else []
            recent = [ts for ts in recent if ts > now.timestamp() - 60]
            if len(recent) >= 3:
                raise AppError(429, "too_many_requests", "每分钟最多生成 3 次绑定码，请稍后再试。")
            for _ in range(5):
                chars = "".join(choice(BINDING_CODE_ALPHABET) for _ in range(8))
                code = f"ZMD-{chars[:4]}-{chars[4:]}"
                digest = _digest(code)
                collision = session.execute(
                    select(AuthBindingCodeRecord.user_id).where(AuthBindingCodeRecord.code_digest == digest)
                ).first()
                if collision is None:
                    break
            else:
                raise AppError(503, "binding_code_unavailable", "暂时无法生成绑定码，请稍后重试。")
            if record is None:
                record = AuthBindingCodeRecord(user_id=user_id)
                session.add(record)
            record.code_digest = digest
            record.created_at = now
            record.expires_at = now + self.ttl
            record.recent_generations_json = json.dumps([*recent, now.timestamp()])
            session.commit()
            return BindingCodeResponse(code=code, expiresAt=now + self.ttl)

    def lookup(self, code: str) -> BindingCodeAccountResponse:
        digest = _digest(code)
        with SessionLocal() as session:
            result = session.execute(
                select(AuthBindingCodeRecord, AuthUserRecord)
                .join(AuthUserRecord, AuthUserRecord.id == AuthBindingCodeRecord.user_id)
                .where(
                    AuthBindingCodeRecord.code_digest == digest,
                    AuthBindingCodeRecord.expires_at > _utcnow(),
                    AuthUserRecord.is_disabled.is_(False),
                )
            ).first()
            if result is None or not compare_digest(result[0].code_digest, digest):
                raise AppError(404, "binding_code_invalid", "绑定码无效或已过期，请重新生成。")
            # No consumption, session creation, cleanup or other DB writes.
            return BindingCodeAccountResponse(accountId=result[1].id, accountDisplayName=result[1].nickname)


binding_code_service = BindingCodeService()
