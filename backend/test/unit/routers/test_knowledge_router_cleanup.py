from inspect import signature
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from server.routers import knowledge_router

pytestmark = pytest.mark.asyncio


class FakeTaskContext:
    def __init__(self):
        self.result = None
        self.payload = {}

    async def set_message(self, message: str) -> None:
        return None

    async def set_progress(self, progress: float, message: str | None = None) -> None:
        return None

    async def set_result(self, result: dict) -> None:
        self.result = result

    async def raise_if_cancelled(self) -> None:
        return None


class CapturingLogger:
    def __init__(self):
        self.entries: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, level):
        def record(*args, **kwargs):
            self.entries.append((level, args, kwargs))

        return record


async def test_all_kb_path_routes_include_object_access_dependency():
    kb_routes = [
        route for route in knowledge_router.knowledge.routes if isinstance(route, APIRoute) and "{kb_id}" in route.path
    ]

    assert kb_routes
    for route in kb_routes:
        dependency_calls = {dependency.call for dependency in route.dependant.dependencies}
        assert knowledge_router._ensure_path_kb_access in dependency_calls


async def test_path_kb_access_rejects_inaccessible_database(monkeypatch):
    class FakeKnowledgeBaseRepository:
        async def get_by_kb_id(self, kb_id):
            return SimpleNamespace(kb_id=kb_id)

    async def deny_access(user_info, kb_id):
        return False

    monkeypatch.setattr(knowledge_router, "KnowledgeBaseRepository", FakeKnowledgeBaseRepository)
    monkeypatch.setattr(knowledge_router.knowledge_base, "check_accessible", deny_access)

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router._ensure_path_kb_access(
            SimpleNamespace(path_params={"kb_id": "kb-private"}),
            current_user=SimpleNamespace(uid="admin-a", role="admin", department_id=1),
        )

    assert exc_info.value.status_code == 403


async def test_delete_database_rejects_active_kb_task(monkeypatch):
    async def find_active_task(**kwargs):
        assert kwargs == {
            "payload_match": {"kb_id": "kb-1"},
            "statuses": {"pending", "running"},
        }
        return SimpleNamespace(id="task-1")

    async def reject_delete(kb_id):
        raise AssertionError("database cleanup must wait for active tasks")

    monkeypatch.setattr(
        knowledge_router.tasker,
        "find_task_by_payload_any_type",
        find_active_task,
    )
    monkeypatch.setattr(knowledge_router.knowledge_base, "delete_database", reject_delete)

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.delete_database(
            "kb-1",
            current_user=SimpleNamespace(uid="admin-a"),
        )

    assert exc_info.value.status_code == 409


async def test_delete_database_removes_terminal_kb_tasks_after_storage_cleanup(monkeypatch):
    calls = []

    async def find_active_task(**kwargs):
        return None

    async def delete_database(kb_id):
        calls.append(("database", kb_id))

    async def delete_terminal_tasks_by_payload(**kwargs):
        calls.append(("tasks", kwargs))
        return 2

    async def reload_all():
        calls.append(("reload", None))

    from yuxi.agents.buildin import agent_manager

    monkeypatch.setattr(knowledge_router.tasker, "find_task_by_payload_any_type", find_active_task)
    monkeypatch.setattr(knowledge_router.knowledge_base, "delete_database", delete_database)
    monkeypatch.setattr(knowledge_router.tasker, "delete_terminal_tasks_by_payload", delete_terminal_tasks_by_payload)
    monkeypatch.setattr(agent_manager, "reload_all", reload_all)

    result = await knowledge_router.delete_database("kb-1", current_user=SimpleNamespace(uid="admin-a"))

    assert result == {"message": "删除成功"}
    assert calls == [
        ("database", "kb-1"),
        ("tasks", {"payload_match": {"kb_id": "kb-1"}}),
        ("reload", None),
    ]


async def test_upload_file_does_not_expose_legacy_allow_jsonl_query():
    assert "allow_jsonl" not in signature(knowledge_router.upload_file).parameters


async def test_document_file_exists_returns_boolean_for_relative_path(monkeypatch):
    captured = {}

    async def fake_ensure_database_supports_documents(kb_id: str, operation: str) -> None:
        captured["ensure"] = (kb_id, operation)

    async def fake_document_file_exists(kb_id: str, filename: str) -> bool:
        captured["exists"] = (kb_id, filename)
        return True

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_database_supports_documents",
        fake_ensure_database_supports_documents,
    )
    monkeypatch.setattr(knowledge_router.knowledge_base, "document_file_exists", fake_document_file_exists)

    result = await knowledge_router.document_file_exists(
        "kb_1",
        filename=" google_drive/shared_drives/engineering/playbook.txt ",
        current_user=SimpleNamespace(uid="user_1"),
    )

    assert result == {
        "kb_id": "kb_1",
        "filename": "google_drive/shared_drives/engineering/playbook.txt",
        "exists": True,
    }
    assert captured == {
        "ensure": ("kb_1", "文档存在性检查"),
        "exists": ("kb_1", "google_drive/shared_drives/engineering/playbook.txt"),
    }


async def test_document_file_exists_route_accepts_filename_with_slashes(monkeypatch):
    async def fake_admin_user():
        return SimpleNamespace(uid="user_1")

    async def allow_path_kb_access():
        return None

    async def fake_ensure_database_supports_documents(kb_id: str, operation: str) -> None:
        return None

    async def fake_document_file_exists(kb_id: str, filename: str) -> bool:
        assert kb_id == "kb_1"
        assert filename == "google_drive/shared_drives/engineering/playbook.txt"
        return True

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_database_supports_documents",
        fake_ensure_database_supports_documents,
    )
    monkeypatch.setattr(knowledge_router.knowledge_base, "document_file_exists", fake_document_file_exists)

    app = FastAPI()
    app.include_router(knowledge_router.knowledge, prefix="/api")
    app.dependency_overrides[knowledge_router.get_admin_user] = fake_admin_user
    app.dependency_overrides[knowledge_router._ensure_path_kb_access] = allow_path_kb_access

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/knowledge/databases/kb_1/documents/exists",
            params={"filename": "google_drive/shared_drives/engineering/playbook.txt"},
        )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "kb_id": "kb_1",
        "filename": "google_drive/shared_drives/engineering/playbook.txt",
        "exists": True,
    }


async def test_document_file_exists_rejects_blank_filename(monkeypatch):
    async def fake_ensure_database_supports_documents(kb_id: str, operation: str) -> None:
        return None

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_database_supports_documents",
        fake_ensure_database_supports_documents,
    )

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.document_file_exists(
            "kb_1",
            filename="   ",
            current_user=SimpleNamespace(uid="user_1"),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "filename is required"


async def test_upload_file_rejects_jsonl_uploads():
    upload = UploadFile(filename="dataset.jsonl", file=BytesIO(b'{"query":"hello"}\n'))

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.upload_file(upload, kb_id=None, current_user=SimpleNamespace(uid="user_1"))

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Unsupported file type: .jsonl"


async def test_upload_file_rejects_oversized_file(monkeypatch):
    monkeypatch.setattr(knowledge_router, "MAX_UPLOAD_SIZE_BYTES", 5)

    async def fake_ensure_database_supports_documents(kb_id: str, operation: str) -> None:
        return None

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_database_supports_documents",
        fake_ensure_database_supports_documents,
    )

    upload = UploadFile(filename="demo.txt", file=BytesIO(b"123456"))

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.upload_file(upload, kb_id="kb_1", current_user=SimpleNamespace(uid="user_1"))

    assert exc_info.value.status_code == 400
    assert "100 MB" in exc_info.value.detail


async def test_upload_file_invalid_kb_fails_before_read_or_minio(monkeypatch):
    calls = {"read": 0, "upload": 0}

    async def fake_ensure_database_supports_documents(kb_id: str, operation: str) -> None:
        raise HTTPException(status_code=404, detail=f"知识库 {kb_id} 不存在")

    async def fake_read_upload_with_limit(*_args, **_kwargs) -> bytes:
        calls["read"] += 1
        return b"demo"

    async def fake_upload_to_minio(*_args, **_kwargs) -> str:
        calls["upload"] += 1
        return "minio://knowledgebases/kb_1/upload/demo.txt"

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_database_supports_documents",
        fake_ensure_database_supports_documents,
    )
    monkeypatch.setattr(knowledge_router, "read_upload_with_limit", fake_read_upload_with_limit)
    monkeypatch.setattr(knowledge_router, "aupload_file_to_minio", fake_upload_to_minio)

    upload = UploadFile(filename="demo.txt", file=BytesIO(b"demo"))

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.upload_file(upload, kb_id="missing", current_user=SimpleNamespace(uid="user_1"))

    assert exc_info.value.status_code == 404
    assert calls == {"read": 0, "upload": 0}


async def test_upload_file_read_only_kb_fails_before_read_or_minio(monkeypatch):
    calls = {"read": 0, "upload": 0}

    async def fake_ensure_database_supports_documents(kb_id: str, operation: str) -> None:
        raise HTTPException(status_code=400, detail="只支持检索，不支持文档上传")

    async def fake_read_upload_with_limit(*_args, **_kwargs) -> bytes:
        calls["read"] += 1
        return b"demo"

    async def fake_upload_to_minio(*_args, **_kwargs) -> str:
        calls["upload"] += 1
        return "minio://knowledgebases/kb_1/upload/demo.txt"

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_database_supports_documents",
        fake_ensure_database_supports_documents,
    )
    monkeypatch.setattr(knowledge_router, "read_upload_with_limit", fake_read_upload_with_limit)
    monkeypatch.setattr(knowledge_router, "aupload_file_to_minio", fake_upload_to_minio)

    upload = UploadFile(filename="demo.txt", file=BytesIO(b"demo"))

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.upload_file(upload, kb_id="readonly", current_user=SimpleNamespace(uid="user_1"))

    assert exc_info.value.status_code == 400
    assert calls == {"read": 0, "upload": 0}


async def test_markdown_endpoint_rejects_oversized_file(monkeypatch):
    monkeypatch.setattr(knowledge_router, "MAX_UPLOAD_SIZE_BYTES", 5)
    upload = UploadFile(filename="demo.txt", file=BytesIO(b"123456"))

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.mark_it_down(upload, current_user=SimpleNamespace(uid="user_1"))

    assert exc_info.value.status_code == 400
    assert "100 MB" in exc_info.value.detail


async def test_index_documents_uses_uid_for_operator(monkeypatch):
    captured = {}

    async def fake_ensure_database_supports_documents(kb_id: str, operation: str) -> None:
        return None

    async def fake_get_database_info(kb_id: str) -> dict:
        return {"name": "测试知识库"}

    async def fake_index_file(kb_id: str, file_id: str, operator_id: str | None = None, params: dict | None = None):
        captured["operator_id"] = operator_id
        return {"file_id": file_id, "status": "indexed"}

    async def fake_enqueue(name: str, task_type: str, payload: dict, coroutine):
        await coroutine(FakeTaskContext())
        return SimpleNamespace(id="task_1")

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_database_supports_documents",
        fake_ensure_database_supports_documents,
    )
    monkeypatch.setattr(knowledge_router.knowledge_base, "get_database_info", fake_get_database_info)
    monkeypatch.setattr(knowledge_router.knowledge_base, "index_file", fake_index_file)
    monkeypatch.setattr(knowledge_router.tasker, "enqueue", fake_enqueue)

    result = await knowledge_router.index_documents(
        "kb_1",
        ["file_1"],
        params={},
        current_user=SimpleNamespace(id="numeric-id", uid="uid-user"),
    )

    assert result["status"] == "queued"
    assert captured["operator_id"] == "uid-user"


async def test_parse_documents_rejects_oversized_direct_batch():
    file_ids = [f"file_{index}" for index in range(knowledge_router.MAX_DIRECT_DOCUMENT_ACTION_FILE_IDS + 1)]

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.parse_documents(
            "kb_1",
            file_ids,
            current_user=SimpleNamespace(uid="uid-user"),
        )

    assert exc_info.value.status_code == 400
    assert str(knowledge_router.MAX_DIRECT_DOCUMENT_ACTION_FILE_IDS) in exc_info.value.detail


async def test_parse_pending_documents_enqueues_status_scoped_task(monkeypatch):
    captured = {"list_calls": [], "parsed": []}

    async def fake_ensure_database_supports_documents(kb_id: str, operation: str) -> None:
        captured["ensure"] = (kb_id, operation)

    async def fake_get_database_info(kb_id: str) -> dict:
        return {"name": "测试知识库", "stats": {"pending_parse_count": 2}}

    async def fake_list_document_file_ids_by_statuses(kb_id: str, *, statuses, after_file_id, limit):
        captured["list_calls"].append(
            {"kb_id": kb_id, "statuses": statuses, "after_file_id": after_file_id, "limit": limit}
        )
        return ["file_1", "file_2"] if after_file_id is None else []

    async def fake_parse_file(kb_id: str, file_id: str, operator_id: str | None = None):
        captured["parsed"].append({"kb_id": kb_id, "file_id": file_id, "operator_id": operator_id})
        return {"file_id": file_id, "status": "parsed"}

    async def fake_enqueue_unique_by_payload(**kwargs):
        captured["payload"] = kwargs["payload"]
        captured["payload_match"] = kwargs["payload_match"]
        captured["statuses"] = kwargs["statuses"]
        await kwargs["coroutine"](FakeTaskContext())
        return SimpleNamespace(id="task_1"), True

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_database_supports_documents",
        fake_ensure_database_supports_documents,
    )
    monkeypatch.setattr(knowledge_router.knowledge_base, "get_database_info", fake_get_database_info)
    monkeypatch.setattr(
        knowledge_router.knowledge_base,
        "list_document_file_ids_by_statuses",
        fake_list_document_file_ids_by_statuses,
    )
    monkeypatch.setattr(knowledge_router.knowledge_base, "parse_file", fake_parse_file)
    monkeypatch.setattr(knowledge_router.tasker, "enqueue_unique_by_payload", fake_enqueue_unique_by_payload)

    result = await knowledge_router.parse_pending_documents(
        "kb_1",
        current_user=SimpleNamespace(uid="uid-user"),
    )

    assert result["status"] == "queued"
    assert result["task_id"] == "task_1"
    assert captured["ensure"] == ("kb_1", "文档解析")
    assert captured["payload_match"] == {"kb_id": "kb_1", "scope": "pending", "action": "parse"}
    assert captured["statuses"] == knowledge_router.ACTIVE_DOCUMENT_ACTION_TASK_STATUSES
    assert captured["payload"]["statuses"] == knowledge_router.PENDING_PARSE_STATUSES
    assert captured["list_calls"] == [
        {
            "kb_id": "kb_1",
            "statuses": knowledge_router.PENDING_PARSE_STATUSES,
            "after_file_id": None,
            "limit": knowledge_router.DOCUMENT_ACTION_BATCH_SIZE,
        },
        {
            "kb_id": "kb_1",
            "statuses": knowledge_router.PENDING_PARSE_STATUSES,
            "after_file_id": "file_2",
            "limit": knowledge_router.DOCUMENT_ACTION_BATCH_SIZE,
        },
    ]
    assert captured["parsed"] == [
        {"kb_id": "kb_1", "file_id": "file_1", "operator_id": "uid-user"},
        {"kb_id": "kb_1", "file_id": "file_2", "operator_id": "uid-user"},
    ]


async def test_document_actions_fail_the_task_after_partial_processing(monkeypatch):
    secret = "Authorization=secret-api-key provider-body=<private>"
    logger = CapturingLogger()
    parse_context = FakeTaskContext()

    async def failing_parse(*_args, **_kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(knowledge_router, "logger", logger)
    monkeypatch.setattr(knowledge_router.knowledge_base, "parse_file", failing_parse)
    with pytest.raises(RuntimeError, match="解析完成，失败 1 个"):
        await knowledge_router._run_parse_file_ids(
            context=parse_context,
            kb_id="kb_1",
            file_ids=["file_1"],
            operator_id="uid-user",
        )
    assert parse_context.result == {
        "items": [{"file_id": "file_1", "status": "failed", "error": "解析失败"}],
        "processed": 1,
        "failed": 1,
    }

    index_context = FakeTaskContext()

    async def failing_index(*_args, **_kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(knowledge_router.knowledge_base, "index_file", failing_index)
    with pytest.raises(RuntimeError, match="入库完成，失败 1 个"):
        await knowledge_router._run_index_file_ids(
            context=index_context,
            kb_id="kb_1",
            file_ids=["file_1"],
            operator_id="uid-user",
            params={},
        )
    assert index_context.result == {
        "items": [{"file_id": "file_1", "status": "failed", "error": "入库失败"}],
        "processed": 1,
        "failed": 1,
    }
    assert secret not in repr(parse_context.result)
    assert secret not in repr(index_context.result)
    assert secret not in repr(logger.entries)


async def test_knowledge_list_failure_does_not_expose_provider_secret(monkeypatch):
    secret = "Authorization=secret-api-key provider-body=<private>"
    logger = CapturingLogger()

    async def fail(_uid):
        raise RuntimeError(secret)

    monkeypatch.setattr(knowledge_router.knowledge_base, "get_databases_by_uid", fail)
    monkeypatch.setattr(knowledge_router, "logger", logger)

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.get_databases(current_user=SimpleNamespace(uid="admin-1"))

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "获取数据库列表失败"
    assert secret not in repr(logger.entries)


async def test_add_uploaded_documents_failure_uses_fixed_item_error(monkeypatch):
    secret = "Authorization=secret-api-key provider-body=<private>"
    item = "minio://knowledgebases/kb_1/upload/demo.txt"
    logger = CapturingLogger()

    async def fake_ensure_database_supports_documents(kb_id: str, operation: str) -> None:
        return None

    async def fail_add(*_args, **_kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_database_supports_documents",
        fake_ensure_database_supports_documents,
    )
    monkeypatch.setattr(knowledge_router.knowledge_base, "add_file_record", fail_add)
    monkeypatch.setattr(knowledge_router, "logger", logger)

    result = await knowledge_router.add_uploaded_documents(
        "kb_1",
        knowledge_router.AddUploadedDocumentsRequest(
            items=[item],
            params={"content_hashes": {item: "hash_1"}},
        ),
        current_user=SimpleNamespace(uid="uid-user"),
    )

    assert result["status"] == "failed"
    assert result["failed_items"][0]["error"] == "添加记录失败"
    assert secret not in repr(result)
    assert secret not in repr(logger.entries)


async def test_knowledge_query_failure_does_not_expose_provider_secret(monkeypatch):
    secret = "Authorization=secret-api-key provider-body=<private>"
    logger = CapturingLogger()

    async def fail(*_args, **_kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(knowledge_router.knowledge_base, "aquery", fail)
    monkeypatch.setattr(knowledge_router, "logger", logger)

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.query_knowledge_base(
            "kb_1",
            query="question",
            meta={},
            current_user=SimpleNamespace(uid="admin-1"),
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "知识库查询失败"
    assert secret not in repr(logger.entries)


async def test_generate_description_failure_does_not_expose_provider_secret(monkeypatch):
    import yuxi.models

    secret = "Authorization=secret-api-key provider-body=<private>"
    logger = CapturingLogger()

    class Model:
        async def call(self, prompt):
            del prompt
            raise RuntimeError(secret)

    monkeypatch.setattr(yuxi.models, "select_model", lambda **_kwargs: Model())
    monkeypatch.setattr(knowledge_router, "logger", logger)

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.generate_description(
            name="research",
            current_description="",
            file_list=[],
            current_user=SimpleNamespace(uid="admin-1"),
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "生成描述失败"
    assert secret not in repr(logger.entries)


async def test_parse_resume_handler_uses_persisted_operator_and_file_ids(monkeypatch):
    context = FakeTaskContext()
    context.payload = {"kb_id": "kb_1", "operator_id": "uid-user", "file_ids": ["file_1"]}
    parsed: list[tuple[str, str, str]] = []

    async def fake_parse(kb_id: str, file_id: str, operator_id: str | None = None):
        parsed.append((kb_id, file_id, operator_id or ""))
        return {"file_id": file_id, "status": "parsed"}

    monkeypatch.setattr(knowledge_router.knowledge_base, "parse_file", fake_parse)
    result = await knowledge_router._resume_parse_task(context)

    assert result["failed"] == 0
    assert parsed == [("kb_1", "file_1", "uid-user")]


async def test_index_pending_documents_uses_pending_statuses_and_params(monkeypatch):
    captured = {"list_calls": [], "updated": [], "indexed": []}

    async def fake_ensure_database_supports_documents(kb_id: str, operation: str) -> None:
        captured["ensure"] = (kb_id, operation)

    async def fake_get_database_info(kb_id: str) -> dict:
        return {"name": "测试知识库", "stats": {"pending_index_count": 2}}

    async def fake_list_document_file_ids_by_statuses(kb_id: str, *, statuses, after_file_id, limit):
        captured["list_calls"].append(
            {"kb_id": kb_id, "statuses": statuses, "after_file_id": after_file_id, "limit": limit}
        )
        return ["file_1", "file_2"] if after_file_id is None else []

    async def fake_update_file_params(kb_id: str, file_id: str, params: dict, operator_id: str | None = None):
        captured["updated"].append({"kb_id": kb_id, "file_id": file_id, "params": params, "operator_id": operator_id})

    async def fake_index_file(kb_id: str, file_id: str, operator_id: str | None = None, params: dict | None = None):
        captured["indexed"].append({"kb_id": kb_id, "file_id": file_id, "operator_id": operator_id, "params": params})
        return {"file_id": file_id, "status": "indexed"}

    async def fake_enqueue_unique_by_payload(**kwargs):
        captured["payload"] = kwargs["payload"]
        captured["payload_match"] = kwargs["payload_match"]
        await kwargs["coroutine"](FakeTaskContext())
        return SimpleNamespace(id="task_1"), True

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_database_supports_documents",
        fake_ensure_database_supports_documents,
    )
    monkeypatch.setattr(knowledge_router.knowledge_base, "get_database_info", fake_get_database_info)
    monkeypatch.setattr(
        knowledge_router.knowledge_base,
        "list_document_file_ids_by_statuses",
        fake_list_document_file_ids_by_statuses,
    )
    monkeypatch.setattr(knowledge_router.knowledge_base, "update_file_params", fake_update_file_params)
    monkeypatch.setattr(knowledge_router.knowledge_base, "index_file", fake_index_file)
    monkeypatch.setattr(knowledge_router.tasker, "enqueue_unique_by_payload", fake_enqueue_unique_by_payload)

    params = {"chunk_preset_id": "general"}
    result = await knowledge_router.index_pending_documents(
        "kb_1",
        payload=knowledge_router.PendingIndexDocumentsRequest(params=params),
        current_user=SimpleNamespace(uid="uid-user"),
    )

    assert result["status"] == "queued"
    assert captured["ensure"] == ("kb_1", "文档入库")
    assert captured["payload_match"] == {"kb_id": "kb_1", "scope": "pending", "action": "index"}
    assert captured["payload"]["statuses"] == knowledge_router.PENDING_INDEX_STATUSES
    assert captured["payload"]["params"] == params
    assert captured["list_calls"][0]["statuses"] == knowledge_router.PENDING_INDEX_STATUSES
    assert captured["updated"] == [
        {"kb_id": "kb_1", "file_id": "file_1", "params": params, "operator_id": "uid-user"},
        {"kb_id": "kb_1", "file_id": "file_2", "params": params, "operator_id": "uid-user"},
    ]
    assert captured["indexed"] == [
        {"kb_id": "kb_1", "file_id": "file_1", "operator_id": "uid-user", "params": params},
        {"kb_id": "kb_1", "file_id": "file_2", "operator_id": "uid-user", "params": params},
    ]


async def test_add_documents_auto_index_returns_one_final_result_per_item(monkeypatch):
    context = FakeTaskContext()
    item = "minio://knowledgebases/kb_1/upload/demo.txt"

    async def fake_ensure_database_supports_documents(kb_id: str, operation: str) -> None:
        return None

    async def fake_get_database_info(kb_id: str) -> dict:
        return {"name": "测试知识库"}

    async def fake_add_file_record(kb_id: str, item_path: str, params: dict, operator_id: str | None = None):
        return {"file_id": "file_1", "status": "indexing"}

    async def fake_parse_file(kb_id: str, file_id: str, operator_id: str | None = None):
        return {"file_id": file_id, "status": "parsed"}

    async def fake_update_file_params(kb_id: str, file_id: str, params: dict, operator_id: str | None = None):
        return None

    async def fake_index_file(kb_id: str, file_id: str, operator_id: str | None = None, params: dict | None = None):
        return {"file_id": file_id, "status": "indexed"}

    async def fake_enqueue(name: str, task_type: str, payload: dict, coroutine):
        await coroutine(context)
        return SimpleNamespace(id="task_1")

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_database_supports_documents",
        fake_ensure_database_supports_documents,
    )
    monkeypatch.setattr(knowledge_router.knowledge_base, "get_database_info", fake_get_database_info)
    monkeypatch.setattr(knowledge_router.knowledge_base, "add_file_record", fake_add_file_record)
    monkeypatch.setattr(knowledge_router.knowledge_base, "parse_file", fake_parse_file)
    monkeypatch.setattr(knowledge_router.knowledge_base, "update_file_params", fake_update_file_params)
    monkeypatch.setattr(knowledge_router.knowledge_base, "index_file", fake_index_file)
    monkeypatch.setattr(knowledge_router.tasker, "enqueue", fake_enqueue)

    result = await knowledge_router.add_documents(
        "kb_1",
        [item],
        params={"content_type": "file", "auto_index": True, "content_hashes": {item: "hash_1"}},
        current_user=SimpleNamespace(uid="uid-user"),
    )

    assert result["status"] == "queued"
    assert context.result["submitted"] == 1
    assert context.result["failed"] == 0
    assert context.result["items"] == [{"file_id": "file_1", "status": "indexed"}]


async def test_add_documents_auto_index_treats_error_none_as_success(monkeypatch):
    """成功入库的文件元数据会携带 error=None，不应被统计为失败 (#793)。"""
    context = FakeTaskContext()
    item = "minio://knowledgebases/kb_1/upload/demo.txt"

    async def fake_ensure_database_supports_documents(kb_id: str, operation: str) -> None:
        return None

    async def fake_get_database_info(kb_id: str) -> dict:
        return {"name": "测试知识库"}

    async def fake_add_file_record(kb_id: str, item_path: str, params: dict, operator_id: str | None = None):
        return {"file_id": "file_1", "status": "indexing"}

    async def fake_parse_file(kb_id: str, file_id: str, operator_id: str | None = None):
        return {"file_id": file_id, "status": "parsed", "error": None}

    async def fake_update_file_params(kb_id: str, file_id: str, params: dict, operator_id: str | None = None):
        return None

    async def fake_index_file(kb_id: str, file_id: str, operator_id: str | None = None, params: dict | None = None):
        return {"file_id": file_id, "status": "indexed", "error": None}

    async def fake_enqueue(name: str, task_type: str, payload: dict, coroutine):
        await coroutine(context)
        return SimpleNamespace(id="task_1")

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_database_supports_documents",
        fake_ensure_database_supports_documents,
    )
    monkeypatch.setattr(knowledge_router.knowledge_base, "get_database_info", fake_get_database_info)
    monkeypatch.setattr(knowledge_router.knowledge_base, "add_file_record", fake_add_file_record)
    monkeypatch.setattr(knowledge_router.knowledge_base, "parse_file", fake_parse_file)
    monkeypatch.setattr(knowledge_router.knowledge_base, "update_file_params", fake_update_file_params)
    monkeypatch.setattr(knowledge_router.knowledge_base, "index_file", fake_index_file)
    monkeypatch.setattr(knowledge_router.tasker, "enqueue", fake_enqueue)

    result = await knowledge_router.add_documents(
        "kb_1",
        [item],
        params={"content_type": "file", "auto_index": True, "content_hashes": {item: "hash_1"}},
        current_user=SimpleNamespace(uid="uid-user"),
    )

    assert result["status"] == "queued"
    assert context.result["submitted"] == 1
    assert context.result["failed"] == 0
    assert context.result["items"] == [{"file_id": "file_1", "status": "indexed", "error": None}]


async def test_add_uploaded_documents_rejects_empty_items(monkeypatch):
    async def fake_ensure_database_supports_documents(kb_id: str, operation: str) -> None:
        return None

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_database_supports_documents",
        fake_ensure_database_supports_documents,
    )

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.add_uploaded_documents(
            "kb_1",
            knowledge_router.AddUploadedDocumentsRequest(items=[], params={}),
            current_user=SimpleNamespace(uid="uid-user"),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "items must not be empty"


async def test_add_uploaded_documents_rejects_non_minio_url(monkeypatch):
    async def fake_ensure_database_supports_documents(kb_id: str, operation: str) -> None:
        return None

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_database_supports_documents",
        fake_ensure_database_supports_documents,
    )

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.add_uploaded_documents(
            "kb_1",
            knowledge_router.AddUploadedDocumentsRequest(
                items=["https://example.com/demo.txt"],
                params={"content_hashes": {"https://example.com/demo.txt": "hash_1"}},
            ),
            current_user=SimpleNamespace(uid="uid-user"),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "File source must be a MinIO URL"


async def test_add_uploaded_documents_rejects_missing_content_hash(monkeypatch):
    item = "minio://knowledgebases/kb_1/upload/demo.txt"

    async def fake_ensure_database_supports_documents(kb_id: str, operation: str) -> None:
        return None

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_database_supports_documents",
        fake_ensure_database_supports_documents,
    )

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.add_uploaded_documents(
            "kb_1",
            knowledge_router.AddUploadedDocumentsRequest(items=[item], params={}),
            current_user=SimpleNamespace(uid="uid-user"),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == f"Missing content_hash for file: {item}"


async def test_add_uploaded_documents_creates_records_without_task(monkeypatch):
    item = "minio://knowledgebases/kb_1/upload/demo.txt"
    captured = {}

    async def fake_ensure_database_supports_documents(kb_id: str, operation: str) -> None:
        return None

    async def fake_add_file_record(kb_id: str, item_path: str, params: dict, operator_id: str | None = None):
        captured["kb_id"] = kb_id
        captured["item"] = item_path
        captured["params"] = params
        captured["operator_id"] = operator_id
        return {"file_id": "file_1", "status": "uploaded", "filename": "demo.txt"}

    async def fail_enqueue(*_args, **_kwargs):
        raise AssertionError("documents/add must not enqueue tasker work")

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_database_supports_documents",
        fake_ensure_database_supports_documents,
    )
    monkeypatch.setattr(knowledge_router.knowledge_base, "add_file_record", fake_add_file_record)
    monkeypatch.setattr(knowledge_router.tasker, "enqueue", fail_enqueue)

    result = await knowledge_router.add_uploaded_documents(
        "kb_1",
        knowledge_router.AddUploadedDocumentsRequest(
            items=[item],
            params={
                "content_hashes": {item: "hash_1"},
                "file_sizes": {item: 4},
                "source_paths": {item: "docs/demo.txt"},
            },
        ),
        current_user=SimpleNamespace(uid="uid-user"),
    )

    assert result["status"] == "success"
    assert result["added"] == 1
    assert result["failed"] == 0
    assert result["items"][0]["file_id"] == "file_1"
    assert captured == {
        "kb_id": "kb_1",
        "item": item,
        "params": {
            "content_hashes": {item: "hash_1"},
            "file_sizes": {item: 4},
            "source_path": "docs/demo.txt",
        },
        "operator_id": "uid-user",
    }
