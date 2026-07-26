from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from server.routers import model_provider_router
from server.routers.model_provider_router import ModelProviderPayload


def test_model_provider_payload_accepts_embedding_and_rerank_urls():
    payload = ModelProviderPayload(
        provider_id="mixed-provider",
        display_name="Mixed Provider",
        base_url="https://api.example.com/v1",
        embedding_base_url="https://api.example.com/v1/embeddings",
        rerank_base_url="https://api.example.com/v1/rerank",
        capabilities=["chat", "embedding", "rerank"],
    )

    data = payload.model_dump(exclude_none=True)

    assert data["embedding_base_url"] == "https://api.example.com/v1/embeddings"
    assert data["rerank_base_url"] == "https://api.example.com/v1/rerank"


@pytest.mark.asyncio
async def test_update_provider_commits_before_refreshing_cache(monkeypatch):
    calls = []

    class Db:
        async def commit(self):
            calls.append("commit")

    class User:
        username = "admin"

    class Provider:
        def to_dict(self):
            return {"provider_id": "alibaba"}

    async def fake_update_provider_config(db, provider_id, data, username):
        calls.append("update")
        return Provider()

    async def fake_refresh_model_cache():
        calls.append("refresh")

    monkeypatch.setattr(model_provider_router, "update_provider_config", fake_update_provider_config)
    monkeypatch.setattr(model_provider_router, "_refresh_model_cache", fake_refresh_model_cache)

    result = await model_provider_router.update_provider(
        "alibaba",
        ModelProviderPayload(enabled_models=[]),
        current_user=User(),
        db=Db(),
    )

    assert result == {"success": True, "data": {"provider_id": "alibaba"}}
    assert calls == ["update", "commit", "refresh"]


@pytest.mark.asyncio
async def test_remote_model_error_does_not_expose_response_body(monkeypatch):
    secret = "Authorization=secret-api-key provider-body=<private>"
    request = httpx.Request("GET", "https://provider.example/models")
    response = httpx.Response(500, request=request, text=secret)

    async def fake_get_provider(_db, _provider_id):
        return object()

    async def fail_fetch(_provider):
        raise httpx.HTTPStatusError(secret, request=request, response=response)

    monkeypatch.setattr(model_provider_router, "get_model_provider_by_id", fake_get_provider)
    monkeypatch.setattr(model_provider_router, "fetch_remote_models", fail_fetch)

    with pytest.raises(HTTPException) as exc_info:
        await model_provider_router.get_remote_models(
            "provider-1",
            current_user=SimpleNamespace(username="admin"),
            db=object(),
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == "远端 Models API 返回 HTTP 500"
    assert secret not in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_model_status_failure_uses_fixed_public_message(monkeypatch):
    secret = "Authorization=secret-api-key provider-body=<private>"
    log_entries: list[tuple] = []

    class FakeLogger:
        def error(self, *args, **kwargs):
            log_entries.append((args, kwargs))

    async def fail(_spec):
        raise RuntimeError(secret)

    monkeypatch.setattr(model_provider_router, "test_model_status_by_spec", fail)
    monkeypatch.setattr(model_provider_router, "logger", FakeLogger())

    result = await model_provider_router.get_model_status_by_spec(
        "provider:model",
        current_user=SimpleNamespace(username="admin"),
    )

    assert result["success"] is False
    assert result["data"]["message"] == "模型状态检查失败"
    assert secret not in repr(result)
    assert secret not in repr(log_entries)


@pytest.mark.asyncio
async def test_update_provider_reports_persisted_state_when_cache_refresh_fails(monkeypatch):
    class Db:
        async def commit(self):
            return None

    class Provider:
        def to_dict(self):
            return {"provider_id": "provider-1"}

    async def fake_update(*_args, **_kwargs):
        return Provider()

    async def fail_refresh():
        raise model_provider_router.ModelCacheRefreshError("internal")

    monkeypatch.setattr(model_provider_router, "update_provider_config", fake_update)
    monkeypatch.setattr(model_provider_router, "_refresh_model_cache", fail_refresh)

    with pytest.raises(HTTPException) as exc_info:
        await model_provider_router.update_provider(
            "provider-1",
            ModelProviderPayload(display_name="Provider"),
            current_user=SimpleNamespace(username="admin"),
            db=Db(),
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "模型供应商已更新，但模型缓存刷新失败"
