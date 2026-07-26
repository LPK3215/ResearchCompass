from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.testclient import TestClient

from server.routers import system_router
from server.routers.system_router import system

pytestmark = pytest.mark.unit


def test_discovery_endpoint_is_public(monkeypatch):
    monkeypatch.setattr("server.routers.system_router.get_version", lambda: "0.7.1.dev0")

    app = FastAPI()
    app.include_router(system, prefix="/api")
    response = TestClient(app).get("/api/system/discovery")

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "ResearchCompass"
    assert payload["version"] == "0.7.1.dev0"
    assert payload["api_prefix"] == "/api"
    assert payload["capabilities"]["cli"]["browser_login"] is True
    assert payload["capabilities"]["cli"]["api_key_auth"] is True
    assert payload["capabilities"]["cli"]["kb_upload"] is True
    assert payload["endpoints"]["cli_auth_sessions"] == "/api/auth/cli/sessions"


@pytest.mark.asyncio
async def test_config_update_reports_runtime_sync_failure(monkeypatch):
    class Config:
        def update(self, items):
            assert items == {"default_model": "provider:model"}

        def save(self):
            return False

        def dump_config(self):
            return {"default_model": "provider:model"}

    monkeypatch.setattr(system_router, "config", Config())

    with pytest.raises(HTTPException) as exc_info:
        await system_router.update_config_batch(
            items={"default_model": "provider:model"},
            current_user=object(),
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "配置已保存，但运行时同步失败"
