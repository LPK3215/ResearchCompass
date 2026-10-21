"""自助账号设置接口的回归：资料更新与修改密码。"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.routers.auth_router import auth
from server.utils.auth_middleware import get_db, get_required_user
from yuxi.storage.postgres.models_business import Base, Department, User
from yuxi.utils.auth_utils import AuthUtils

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]

OLD_PASSWORD = "old-password-123"
NEW_PASSWORD = "new-password-456"
OTHER_PHONE = "13900000000"


@pytest_asyncio.fixture()
async def app_client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        department = Department(name="默认部门")
        user = User(
            username="Member",
            uid="member",
            password_hash=AuthUtils.hash_password(OLD_PASSWORD),
            role="user",
            department=department,
        )
        other = User(
            username="Other",
            uid="other",
            password_hash=AuthUtils.hash_password(OLD_PASSWORD),
            role="user",
            phone_number=OTHER_PHONE,
            department=department,
        )
        db.add_all([department, user, other])
        await db.commit()
        await db.refresh(user)
        await db.refresh(other)

        app = FastAPI()
        app.include_router(auth, prefix="/api")

        async def override_db():
            yield db

        async def override_user():
            return user

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_required_user] = override_user

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client, db, user
    await engine.dispose()


async def test_change_password_rejects_wrong_current_password(app_client):
    client, _, user = app_client

    response = await client.post(
        "/api/auth/password", json={"current_password": "wrong-password", "new_password": NEW_PASSWORD}
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "当前密码不正确"
    assert AuthUtils.verify_password(user.password_hash, OLD_PASSWORD)


async def test_change_password_rejects_same_password(app_client):
    client, _, _ = app_client

    response = await client.post(
        "/api/auth/password", json={"current_password": OLD_PASSWORD, "new_password": OLD_PASSWORD}
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "新密码不能与当前密码相同"


async def test_change_password_updates_hash_for_self_service(app_client):
    client, db, user = app_client

    response = await client.post(
        "/api/auth/password", json={"current_password": OLD_PASSWORD, "new_password": NEW_PASSWORD}
    )

    assert response.status_code == 200, response.text
    assert response.json()["message"] == "密码已更新"
    await db.refresh(user)
    assert AuthUtils.verify_password(user.password_hash, NEW_PASSWORD)
    assert not AuthUtils.verify_password(user.password_hash, OLD_PASSWORD)


async def test_change_password_rejects_short_new_password(app_client):
    client, _, _ = app_client

    response = await client.post("/api/auth/password", json={"current_password": OLD_PASSWORD, "new_password": "short"})

    assert response.status_code == 422


async def test_update_profile_rejects_duplicate_username(app_client):
    client, _, _ = app_client

    response = await client.put("/api/auth/profile", json={"username": "Other"})

    assert response.status_code == 400
    assert response.json()["detail"] == "用户名已存在"


async def test_update_profile_rejects_duplicate_phone(app_client):
    client, _, _ = app_client

    response = await client.put("/api/auth/profile", json={"phone_number": OTHER_PHONE})

    assert response.status_code == 400
    assert response.json()["detail"] == "手机号已被其他用户使用"


async def test_update_profile_persists_username_and_phone(app_client):
    client, db, user = app_client

    response = await client.put("/api/auth/profile", json={"username": "Renamed", "phone_number": "13800138000"})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["username"] == "Renamed"
    assert payload["phone_number"] == "13800138000"
    await db.refresh(user)
    assert user.username == "Renamed"
    assert user.phone_number == "13800138000"
    assert user.uid == "member"
