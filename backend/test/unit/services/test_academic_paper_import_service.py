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
        assert kwargs["coroutine"] is academic_paper_import_service._resume_external_paper_import_task
        calls += 1
        if calls == 1:
            raise RuntimeError("queue unavailable Authorization=secret-api-key")
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
            "data": {"status": "failed", "error_message": "外部论文导入恢复任务提交失败"},
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


@pytest.mark.asyncio
async def test_import_execution_failure_does_not_persist_provider_secret(monkeypatch):
    secret = "Authorization=secret-api-key provider-body=<private>"
    updates = []

    class FakeFileRepository:
        async def update_fields(self, **kwargs):
            updates.append(kwargs)

    class Context:
        async def raise_if_cancelled(self):
            return None

    async def allow_owner(**kwargs):
        return SimpleNamespace(uid="admin-1")

    async def fail_file_info(*args, **kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(academic_paper_import_service, "KnowledgeFileRepository", FakeFileRepository)
    monkeypatch.setattr(academic_paper_import_service, "_ensure_import_owner_can_write", allow_owner)
    monkeypatch.setattr(
        academic_paper_import_service.knowledge_base,
        "get_file_basic_info",
        fail_file_info,
    )

    with pytest.raises(academic_paper_import_service.AcademicPaperImportError) as exc_info:
        await academic_paper_import_service._run_import_task(
            Context(),
            kb_id="kb-1",
            file_id="file-1",
            paper_metadata={"paper_id": "paper-1"},
            operator_id="admin-1",
        )

    assert exc_info.value.error_type == "paper_import_failed"
    assert exc_info.value.message == "外部论文解析与索引失败"
    assert updates[-1]["data"] == {"status": "failed", "error_message": "外部论文解析与索引失败"}
    assert secret not in repr(updates)


@pytest.mark.asyncio
async def test_import_enqueue_failure_returns_fixed_error_and_cleans_artifacts(monkeypatch):
    secret = "Authorization=secret-api-key provider-body=<private>"
    deleted_files = []
    deleted_objects = []

    class FakeSemanticScholarClient:
        async def get_paper(self, identifier):
            return {
                "paperId": "remote-1",
                "title": "A paper",
                "authors": [],
                "externalIds": {},
                "openAccessPdf": {"url": "https://papers.example/paper.pdf"},
            }

        async def download_open_access_pdf(self, url):
            return b"%PDF-1.7 test", url

    class FakePaperRepository:
        async def find_duplicate_for_import(self, **kwargs):
            return None

    class FakeMinioClient:
        async def aupload_file(self, **kwargs):
            return SimpleNamespace(url="minio://documents/paper.pdf")

        async def adelete_file(self, bucket, object_name):
            deleted_objects.append((bucket, object_name))

    async def allow_access(*args, **kwargs):
        return None

    async def get_support(kb_id):
        return None, True

    async def not_duplicate(kb_id, content_hash):
        return False

    async def content_hash(data):
        return "content-hash"

    async def add_file_record(*args, **kwargs):
        return {"file_id": "file-1"}

    async def delete_file(kb_id, file_id):
        deleted_files.append((kb_id, file_id))

    async def fail_enqueue(**kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(academic_paper_import_service, "_ensure_access", allow_access)
    monkeypatch.setattr(academic_paper_import_service, "SemanticScholarClient", FakeSemanticScholarClient)
    monkeypatch.setattr(academic_paper_import_service, "AcademicPaperRepository", FakePaperRepository)
    monkeypatch.setattr(academic_paper_import_service, "get_minio_client", lambda: FakeMinioClient())
    monkeypatch.setattr(academic_paper_import_service, "calculate_content_hash", content_hash)
    monkeypatch.setattr(academic_paper_import_service.knowledge_base, "get_database_document_support", get_support)
    monkeypatch.setattr(academic_paper_import_service.knowledge_base, "file_existed_in_db", not_duplicate)
    monkeypatch.setattr(academic_paper_import_service.knowledge_base, "add_file_record", add_file_record)
    monkeypatch.setattr(academic_paper_import_service.knowledge_base, "delete_file", delete_file)
    monkeypatch.setattr(academic_paper_import_service.tasker, "enqueue", fail_enqueue)

    with pytest.raises(academic_paper_import_service.AcademicPaperImportError) as exc_info:
        await academic_paper_import_service.import_external_paper(
            identifier="remote-1",
            kb_id="kb-1",
            current_user=SimpleNamespace(uid="admin-1"),
        )

    assert exc_info.value.error_type == "paper_import_enqueue_failed"
    assert exc_info.value.message == "外部论文导入任务提交失败"
    assert secret not in str(exc_info.value)
    assert deleted_files == [("kb-1", "file-1")]
    assert len(deleted_objects) == 1
