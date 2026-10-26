from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import logging
import re
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, select

from app.core.binding_code_security import BindingCodeAccessLogFilter, LOOKUP_PATH, LookupRateLimiter
from app.db.models import AuthBindingCodeRecord, AuthSessionRecord, AuthUserRecord
from app.db.session import SessionLocal, engine
from app.main import create_app
from app.schemas.binding_code import BINDING_CODE_PATTERN
from app.services.auth import DatabaseAuthService, SESSION_COOKIE_NAME
from app.services import binding_codes


@pytest.fixture
def account():
    key = uuid4().hex
    token = f"binding-test-{key}"
    now = datetime.now(UTC)
    with SessionLocal() as session:
        session.add(AuthUserRecord(id=key, email=f"{key}@example.com", nickname=f"test-{key}",
                                   created_at=now, is_disabled=False, is_admin=False))
        session.add(AuthSessionRecord(token=DatabaseAuthService._hash_secret(token), user_id=key,
                                      email=f"{key}@example.com", nickname=f"test-{key}", client_type="web",
                                      created_at=now, expires_at=now + timedelta(days=1)))
        session.commit()
    return {"id": key, "nickname": f"test-{key}", "token": token}


@pytest.fixture
def client():
    with TestClient(create_app()) as value:
        yield value


def generate(client, account):
    return client.post("/api/auth/binding-code", headers={"Authorization": f"Bearer {account['token']}"})


def lookup(client, code):
    return client.get(LOOKUP_PATH, params={"code": code})


def test_anonymous_generation_and_invalid_session_are_rejected(client):
    for headers in ({}, {"Authorization": "Bearer expired-token"}):
        response = client.post("/api/auth/binding-code", headers=headers)
        assert response.status_code == 401
        assert response.headers["cache-control"] == "no-store"


def test_generation_ttl_rotation_readonly_lookup_and_private_fields(client, account):
    now = datetime.now(UTC)
    generated = generate(client, account)
    assert generated.status_code == 200
    assert generated.headers["cache-control"] == "no-store"
    data = generated.json()
    assert set(data) == {"code", "expiresAt"}
    assert re.fullmatch(BINDING_CODE_PATTERN, data["code"])
    expiry = datetime.fromisoformat(data["expiresAt"].replace("Z", "+00:00"))
    assert 595 <= (expiry - now).total_seconds() <= 605
    with SessionLocal() as session:
        record = session.get(AuthBindingCodeRecord, account["id"])
        assert record.code_digest != data["code"]
        before = (record.code_digest, record.expires_at, record.recent_generations_json)

    statements = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.strip().split()[0].upper())
    event.listen(engine, "before_cursor_execute", capture)
    try:
        for _ in range(2):
            response = lookup(client, data["code"])
            assert response.status_code == 200
            assert response.json() == {"accountId": account["id"], "accountDisplayName": account["nickname"]}
            assert response.headers["cache-control"] == "no-store"
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert not ({"INSERT", "UPDATE", "DELETE"} & set(statements))
    with SessionLocal() as session:
        record = session.get(AuthBindingCodeRecord, account["id"])
        assert before == (record.code_digest, record.expires_at, record.recent_generations_json)

    rotated = generate(client, account).json()
    assert rotated["code"] != data["code"]
    assert lookup(client, data["code"]).json()["error"]["code"] == "binding_code_invalid"
    assert lookup(client, rotated["code"]).status_code == 200


def test_expiry_and_disabled_accounts_are_indistinguishable(client, account, monkeypatch):
    data = generate(client, account).json()
    with SessionLocal() as session:
        user = session.get(AuthUserRecord, account["id"])
        user.is_disabled = True
        session.commit()
    disabled = lookup(client, data["code"])
    assert disabled.status_code == 404
    assert disabled.headers["cache-control"] == "no-store"
    with SessionLocal() as session:
        session.get(AuthUserRecord, account["id"]).is_disabled = False
        session.commit()
    expiry = datetime.fromisoformat(data["expiresAt"].replace("Z", "+00:00"))
    monkeypatch.setattr(binding_codes, "_utcnow", lambda: expiry)
    expired = lookup(client, data["code"])
    missing = lookup(client, "ZMD-AAAA-BBBB")
    assert expired.status_code == missing.status_code == 404
    assert expired.json() == missing.json() == disabled.json()


def test_generation_limit_survives_service_recreation(client, account, monkeypatch):
    now = datetime.now(UTC)
    monkeypatch.setattr(binding_codes, "_utcnow", lambda: now)
    for _ in range(3):
        assert generate(client, account).status_code == 200
    assert generate(client, account).status_code == 429
    with pytest.raises(Exception) as exc:
        binding_codes.BindingCodeService().generate(account["id"])
    assert exc.value.code == "too_many_requests"
    monkeypatch.setattr(binding_codes, "_utcnow", lambda: now + timedelta(seconds=60))
    assert generate(client, account).status_code == 200


def test_concurrent_generation_leaves_one_current_code(account):
    service = binding_codes.binding_code_service
    with ThreadPoolExecutor(max_workers=3) as pool:
        codes = list(pool.map(lambda _: service.generate(account["id"]).code, range(3)))
    valid = []
    for code in codes:
        try:
            valid.append(service.lookup(code))
        except Exception as exc:
            assert exc.code == "binding_code_invalid"
    assert len(valid) == 1
    with SessionLocal() as session:
        assert len(session.scalars(select(AuthBindingCodeRecord).where(AuthBindingCodeRecord.user_id == account["id"])).all()) == 1


def test_malformed_codes_rate_limit_and_spoofed_forwarded_headers(client):
    for i in range(10):
        response = client.get(LOOKUP_PATH, params={"code": "bad"}, headers={"X-Forwarded-For": f"192.0.2.{i}"})
        assert response.status_code == 422
        assert response.headers["cache-control"] == "no-store"
    response = client.get(LOOKUP_PATH)
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "too_many_requests"
    assert 1 <= int(response.headers["retry-after"]) <= 60
    assert response.headers["cache-control"] == "no-store"


def test_missing_query_and_cookie_csrf(client, account):
    assert client.get(LOOKUP_PATH).status_code == 422
    client.cookies.set(SESSION_COOKIE_NAME, account["token"])
    response = client.post("/api/auth/binding-code", headers={"Origin": "https://untrusted.example"})
    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store"
    assert client.post("/api/auth/binding-code", headers={"Origin": "http://localhost:3000"}).status_code == 200


def test_ip_limiter_expires_buckets(monkeypatch):
    import app.core.binding_code_security as security
    now = [0.0]
    monkeypatch.setattr(security, "monotonic", lambda: now[0])
    limiter = LookupRateLimiter(limit=2)
    assert limiter.retry_after("a") == limiter.retry_after("a") == 0
    assert limiter.retry_after("a") == 60
    assert limiter.retry_after("b") == 0
    now[0] = 60.0
    assert limiter.retry_after("a") == 0
    assert len(limiter._buckets) == 1


def test_access_log_redacts_binding_code():
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                               ("127.0.0.1", "GET", LOOKUP_PATH + "?code=ZMD-ABCD-EFGH", "1.1", 200), None)
    BindingCodeAccessLogFilter().filter(record)
    assert "ZMD-ABCD-EFGH" not in record.getMessage()
    assert "<redacted>" in record.getMessage()
