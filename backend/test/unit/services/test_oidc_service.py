from __future__ import annotations

import os
from urllib.parse import unquote

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

os.environ.setdefault("OPENAI_API_KEY", "dummy")

from yuxi.services import oidc_service
from yuxi.storage.postgres.models_business import User


pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


@pytest_asyncio.fixture
async def oidc_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(User.__table__.create)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


async def _create_user(session, uid: str = "alice") -> User:
    user = User(username="alice", uid=uid, password_hash="x", role="user", is_deleted=0)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def test_find_user_by_oidc_sub_resolves_placeholder_when_sub_contains_colon(oidc_session):
    user = await _create_user(oidc_session)

    await oidc_service._create_oidc_binding_placeholder(oidc_session, "tenant:user", user)

    resolved = await oidc_service.find_user_by_oidc_sub(oidc_session, "tenant:user")

    assert resolved is not None
    assert resolved.id == user.id
    assert resolved.uid == user.uid
    assert resolved.is_deleted == 0


async def test_find_deleted_oidc_user_by_sub_resolves_deleted_target_when_sub_contains_colon(oidc_session):
    user = await _create_user(oidc_session)
    user.is_deleted = 1
    await oidc_session.commit()

    await oidc_service._create_oidc_binding_placeholder(oidc_session, "tenant:user", user)

    resolved = await oidc_service.find_deleted_oidc_user_by_sub(oidc_session, "tenant:user")

    assert resolved is not None
    assert resolved.id == user.id
    assert resolved.uid == user.uid
    assert resolved.is_deleted == 1


async def test_oidc_callback_allows_existing_binding_when_sub_contains_colon(oidc_session, monkeypatch):
    user = await _create_user(oidc_session)
    await oidc_service._create_oidc_binding_placeholder(oidc_session, "tenant:user", user)

    monkeypatch.setattr(oidc_service.oidc_config, "enabled", True)
    monkeypatch.setattr(oidc_service.oidc_config, "client_id", "cid")
    monkeypatch.setattr(oidc_service.oidc_config, "client_secret", "secret")
    monkeypatch.setattr(oidc_service.oidc_config, "token_endpoint", "https://example/token")
    monkeypatch.setattr(oidc_service.oidc_config, "authorization_endpoint", "https://example/auth")
    monkeypatch.setattr(oidc_service.oidc_config, "userinfo_endpoint", "https://example/userinfo")
    monkeypatch.setattr(oidc_service.oidc_config, "use_raw_username", True)
    monkeypatch.setattr(oidc_service.oidc_config, "auto_create_user", False)

    monkeypatch.setattr(
        oidc_service.OIDCUtils,
        "verify_state",
        classmethod(lambda cls, state: {"redirect_path": "/"}),
    )

    async def fake_exchange(cls, code):
        return {"access_token": "token"}

    async def fake_userinfo(cls, access_token):
        return {"sub": "tenant:user", "preferred_username": "alice"}

    async def fake_log_operation(db, user_id, operation, request=None):
        return None

    monkeypatch.setattr(oidc_service.OIDCUtils, "exchange_code_for_token", classmethod(fake_exchange))
    monkeypatch.setattr(oidc_service.OIDCUtils, "get_userinfo", classmethod(fake_userinfo))
    monkeypatch.setattr(oidc_service, "log_operation", fake_log_operation)

    response = await oidc_service.oidc_callback_handler("dummy-code", "dummy-state", oidc_session)

    assert response.status_code == 302
    assert unquote(response.headers["location"]).startswith("/auth/oidc/callback?code=")


async def test_oidc_discovery_ignores_environment_proxy(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"authorization_endpoint": "https://issuer.example/authorize"}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return False

        async def get(self, _url, **_kwargs):
            return Response()

    def async_client(**kwargs):
        captured.update(kwargs)
        return Client()

    monkeypatch.setattr(oidc_service.httpx, "AsyncClient", async_client)

    metadata = oidc_service.OIDCProviderMetadata()
    assert await metadata.load("https://issuer.example") is True
    assert captured["trust_env"] is False


async def test_oidc_pending_state_store_is_bounded(monkeypatch):
    monkeypatch.setattr(oidc_service, "OIDC_MAX_PENDING_STATES", 2)
    tokens = iter(["state-1", "state-2", "state-3"])
    monkeypatch.setattr(oidc_service.secrets, "token_urlsafe", lambda _size: next(tokens))
    oidc_service.OIDCUtils._state_store = {}

    try:
        oidc_service.OIDCUtils.generate_state("/one")
        oidc_service.OIDCUtils.generate_state("/two")
        oidc_service.OIDCUtils.generate_state("/three")

        assert set(oidc_service.OIDCUtils._state_store) == {"state-2", "state-3"}
    finally:
        oidc_service.OIDCUtils._state_store = {}


async def test_oidc_login_url_does_not_expose_metadata_error(monkeypatch):
    secret = "Authorization=secret-api-key provider-body=<private>"
    monkeypatch.setattr(oidc_service.oidc_config, "enabled", True)
    monkeypatch.setattr(oidc_service.oidc_config, "client_id", "client")
    monkeypatch.setattr(oidc_service.oidc_config, "authorization_endpoint", "https://issuer.example/auth")
    monkeypatch.setattr(oidc_service.OIDCUtils, "_last_metadata_error", secret)

    async def fail_build(cls, _redirect_path):
        return None

    monkeypatch.setattr(oidc_service.OIDCUtils, "build_authorization_url", classmethod(fail_build))

    with pytest.raises(HTTPException) as exc_info:
        await oidc_service.oidc_login_url_handler("/")

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "生成登录链接失败，请稍后重试或联系管理员"
    assert secret not in str(exc_info.value.detail)
