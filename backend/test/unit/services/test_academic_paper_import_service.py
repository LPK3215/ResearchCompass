"""academic_paper_import_service 恢复链路单元测试。"""

from types import SimpleNamespace

import pytest

from yuxi.services import academic_paper_import_service


def _record(*, file_id: str, created_by: str | None = "user-1"):
    return SimpleNamespace(
        kb_id="kb-1",
        file_id=file_id,
        filename=f"{file_id}.pdf",
        status="uploaded",
        created_by=created_by,
        processing_params={
            "external_import": {"provider": "semantic_scholar"},
            "chunk_parser_config": {"paper_metadata": {"paper_id": f"s2_{file_id}"}},
        },
    )


@pytest.mark.asyncio
async def test_recovery_continues_after_one_enqueue_failure(monkeypatch):
    updates = []
    records = [_record(file_id="file-1"), _record(file_id="file-2")]

    class FakeKnowledgeBaseRepository:
        async def get_all(self):
            return [SimpleNamespace(kb_id="kb-1")]

    class FakeFileRepository:
        async def list_by_kb_id(self, kb_id):
            return records

        async def update_fields(self, **kwargs):
            updates.append(kwargs)

    class FakeUserRepository:
        async def get_by_uid(self, uid):
            return SimpleNamespace(uid=uid, is_deleted=False)

    calls = 0

    async def enqueue(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("queue unavailable")
        return SimpleNamespace(id="task-2"), True

    async def allow_access(*args, **kwargs):
        return None

    monkeypatch.setattr(academic_paper_import_service, "KnowledgeBaseRepository", FakeKnowledgeBaseRepository)
    monkeypatch.setattr(academic_paper_import_service, "KnowledgeFileRepository", FakeFileRepository)
    monkeypatch.setattr(academic_paper_import_service, "UserRepository", FakeUserRepository)
    monkeypatch.setattr(academic_paper_import_service, "_ensure_access", allow_access)
    monkeypatch.setattr(academic_paper_import_service.tasker, "enqueue_unique_by_payload", enqueue)

    assert await academic_paper_import_service.recover_external_paper_imports() == 1
    assert updates == [
        {
            "file_id": "file-1",
            "kb_id": "kb-1",
            "data": {"status": "failed", "error_message": "恢复任务入队失败: queue unavailable"},
        }
    ]


@pytest.mark.asyncio
async def test_recovery_never_substitutes_system_for_missing_owner(monkeypatch):
    updates = []

    class FakeKnowledgeBaseRepository:
        async def get_all(self):
            return [SimpleNamespace(kb_id="kb-1")]

    class FakeFileRepository:
        async def list_by_kb_id(self, kb_id):
            return [_record(file_id="file-1", created_by=None)]

        async def update_fields(self, **kwargs):
            updates.append(kwargs)

    class FakeUserRepository:
        async def get_by_uid(self, uid):
            return None

    monkeypatch.setattr(academic_paper_import_service, "KnowledgeBaseRepository", FakeKnowledgeBaseRepository)
    monkeypatch.setattr(academic_paper_import_service, "KnowledgeFileRepository", FakeFileRepository)
    monkeypatch.setattr(academic_paper_import_service, "UserRepository", FakeUserRepository)

    assert await academic_paper_import_service.recover_external_paper_imports() == 0
    assert updates == [
        {
            "file_id": "file-1",
            "kb_id": "kb-1",
            "data": {"status": "failed", "error_message": "外部论文导入任务所有者不存在或已删除"},
        }
    ]


@pytest.mark.asyncio
async def test_resume_import_resets_interrupted_parse_and_reuses_payload(monkeypatch):
    updates = []
    calls = []

    class FakeFileRepository:
        async def update_fields(self, **kwargs):
            updates.append(kwargs)

    class FakeUserRepository:
        async def get_by_uid(self, uid):
            assert uid == "admin-1"
            return SimpleNamespace(uid=uid, is_deleted=False)

    async def allow_access(user, kb_id, *, write=False):
        calls.append(("access", user.uid, kb_id, write))

    async def get_file_basic_info(kb_id, file_id):
        assert (kb_id, file_id) == ("kb-1", "file-1")
        return {"meta": {"status": "parsing"}}

    async def run_import(context, **kwargs):
        calls.append(("run", context, kwargs))
        return {"status": "indexed"}

    monkeypatch.setattr(academic_paper_import_service, "KnowledgeFileRepository", FakeFileRepository)
    monkeypatch.setattr(academic_paper_import_service, "UserRepository", FakeUserRepository)
    monkeypatch.setattr(academic_paper_import_service, "_ensure_access", allow_access)
    monkeypatch.setattr(
        academic_paper_import_service.knowledge_base,
        "get_file_basic_info",
        get_file_basic_info,
    )
    monkeypatch.setattr(academic_paper_import_service, "_run_import_task", run_import)
    metadata = {"paper_id": "s2_paper-1"}
    context = SimpleNamespace(
        payload={
            "kb_id": "kb-1",
            "file_id": "file-1",
            "paper_metadata": metadata,
            "operator_id": "admin-1",
        }
    )

    result = await academic_paper_import_service._resume_external_paper_import_task(context)

    assert result == {"status": "indexed"}
    assert calls[0] == ("access", "admin-1", "kb-1", True)
    assert calls[1][2]["paper_metadata"] == metadata
    assert updates == [
        {
            "file_id": "file-1",
            "kb_id": "kb-1",
            "data": {"status": "uploaded", "error_message": "服务重启后恢复外部论文解析"},
        }
    ]
    assert academic_paper_import_service.tasker._resumable_handlers["academic_paper_import"] is (
        academic_paper_import_service._resume_external_paper_import_task
    )
