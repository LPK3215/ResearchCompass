from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from server.routers import auth_router, dashboard_router, external_kb_router, graph_router, mcp_router


SECRET = "Authorization=secret-api-key provider-body=<private>"


class _CapturingLogger:
    def __init__(self):
        self.entries: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, level: str):
        def record(*args, **kwargs):
            self.entries.append((level, args, kwargs))

        return record


@pytest.mark.asyncio
async def test_mcp_list_unknown_failure_is_sanitized(monkeypatch: pytest.MonkeyPatch):
    logger = _CapturingLogger()

    async def fail(_db):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(mcp_router, "get_all_mcp_servers", fail)
    monkeypatch.setattr(mcp_router, "logger", logger)

    with pytest.raises(HTTPException) as exc_info:
        await mcp_router.get_mcp_servers(
            current_user=SimpleNamespace(role="admin"),
            db=object(),
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "获取 MCP 服务器失败"
    assert SECRET not in repr(logger.entries)


@pytest.mark.asyncio
async def test_graph_list_unknown_failure_is_sanitized(monkeypatch: pytest.MonkeyPatch):
    logger = _CapturingLogger()

    async def fail(_uid):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(graph_router.knowledge_base, "get_databases_by_uid", fail)
    monkeypatch.setattr(graph_router, "logger", logger)

    with pytest.raises(HTTPException) as exc_info:
        await graph_router.get_graphs(current_user=SimpleNamespace(uid="user-1"))

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "获取知识图谱列表失败"
    assert SECRET not in repr(logger.entries)


@pytest.mark.asyncio
async def test_external_retrieval_unknown_failure_is_sanitized(monkeypatch: pytest.MonkeyPatch):
    logger = _CapturingLogger()

    async def allow_access(*_args, **_kwargs):
        return {}

    async def fail(*_args, **_kwargs):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(external_kb_router, "_require_accessible_kb", allow_access)
    monkeypatch.setattr(external_kb_router.knowledge_base, "retrieve", fail)
    monkeypatch.setattr(external_kb_router, "logger", logger)

    with pytest.raises(HTTPException) as exc_info:
        await external_kb_router.retrieve_external(
            kb_id="kb-1",
            payload=external_kb_router.ExternalRetrieveRequest(query="research question"),
            current_user=SimpleNamespace(uid="user-1"),
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "知识库查询失败"
    assert SECRET not in repr(logger.entries)


@pytest.mark.asyncio
async def test_dashboard_unknown_failure_is_sanitized(monkeypatch: pytest.MonkeyPatch):
    logger = _CapturingLogger()

    class FailingSession:
        async def execute(self, _query):
            raise RuntimeError(SECRET)

    monkeypatch.setattr(dashboard_router, "logger", logger)

    with pytest.raises(HTTPException) as exc_info:
        await dashboard_router.get_all_conversations(
            uid=None,
            agent_id=None,
            status="active",
            limit=100,
            offset=0,
            db=FailingSession(),
            current_user=SimpleNamespace(uid="superadmin"),
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "获取对话列表失败"
    assert SECRET not in repr(logger.entries)


@pytest.mark.asyncio
async def test_avatar_upload_unknown_failure_is_sanitized(monkeypatch: pytest.MonkeyPatch):
    logger = _CapturingLogger()

    async def fail(*_args, **_kwargs):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(auth_router, "upload_image_to_minio", fail)
    monkeypatch.setattr(auth_router, "logger", logger)

    with pytest.raises(HTTPException) as exc_info:
        await auth_router.upload_user_avatar(
            file=object(),
            current_user=SimpleNamespace(id=1, avatar=None),
            db=object(),
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "头像上传失败"
    assert SECRET not in repr(logger.entries)
