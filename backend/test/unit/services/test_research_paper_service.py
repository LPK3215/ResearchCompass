"""research_paper_service 的边界行为测试。"""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from yuxi.services import research_paper_service


@pytest.mark.asyncio
async def test_remove_paper_tag_normalizes_whitespace(monkeypatch):
    calls = []

    async def allow_access(*args, **kwargs):
        return None

    class FakeRepository:
        async def remove_tag(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(research_paper_service, "_ensure_access", allow_access)
    monkeypatch.setattr(research_paper_service, "AcademicPaperRepository", FakeRepository)

    result = await research_paper_service.remove_paper_tag_view(
        kb_id="kb-1",
        paper_id="paper-1",
        tag="  transformer  ",
        current_user=SimpleNamespace(uid="user-1"),
    )

    assert result == {"paper_id": "paper-1", "tag": "transformer", "removed": True}
    assert calls == [{"kb_id": "kb-1", "paper_id": "paper-1", "uid": "user-1", "tag": "transformer"}]


@pytest.mark.asyncio
async def test_remove_paper_tag_rejects_blank_tag(monkeypatch):
    async def allow_access(*args, **kwargs):
        return None

    monkeypatch.setattr(research_paper_service, "_ensure_access", allow_access)

    with pytest.raises(HTTPException) as exc_info:
        await research_paper_service.remove_paper_tag_view(
            kb_id="kb-1",
            paper_id="paper-1",
            tag="   ",
            current_user=SimpleNamespace(uid="user-1"),
        )

    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_get_paper_view_reads_academic_metadata_without_loading_content(monkeypatch):
    async def allow_access(*args, **kwargs):
        return None

    paper = SimpleNamespace(
        paper_id="paper-1",
        kb_id="kb-1",
        file_id="file-1",
        title="Test Paper",
        abstract=None,
        authors=[],
        publication_year=2024,
        venue=None,
        doi=None,
        keywords=[],
        language="en",
        external_ids={},
        citation_count=0,
        metadata_source="document",
        metadata_status="extracted",
        metadata_error=None,
        metadata_revision=1,
        indexed_revision=1,
        created_at=None,
        updated_at=None,
    )

    class FakePaperRepository:
        async def get_by_paper_id(self, **kwargs):
            return paper

    class FakeFileRepository:
        async def get_by_file_id(self, file_id):
            return SimpleNamespace(filename="paper.pdf", status="indexed", file_type="pdf")

    class FakeChunkRepository:
        async def list_academic_chunk_metadata_by_file_id(self, file_id):
            return [
                (0, {"document_type": "academic_paper", "section_type": "abstract", "section_title": "Abstract"}),
                (1, {"document_type": "academic_paper", "section_type": "method", "section_title": "Method"}),
            ]

        async def list_by_file_id(self, file_id):
            raise AssertionError("paper details must not load chunk content")

    monkeypatch.setattr(research_paper_service, "_ensure_access", allow_access)
    monkeypatch.setattr(research_paper_service, "AcademicPaperRepository", FakePaperRepository)
    monkeypatch.setattr(research_paper_service, "KnowledgeFileRepository", FakeFileRepository)
    monkeypatch.setattr(research_paper_service, "KnowledgeChunkRepository", FakeChunkRepository)

    result = await research_paper_service.get_paper_view(
        kb_id="kb-1",
        paper_id="paper-1",
        current_user=SimpleNamespace(uid="user-1"),
    )

    assert result["file"]["chunk_count"] == 2
    assert [section["section_type"] for section in result["sections"]] == ["abstract", "method"]


@pytest.mark.asyncio
async def test_recover_pending_reindexes_continues_after_enqueue_failure(monkeypatch):
    failed = []
    papers = [
        SimpleNamespace(kb_id="kb-1", paper_id="paper-1", file_id="file-1", title="One", metadata_revision=2),
        SimpleNamespace(kb_id="kb-1", paper_id="paper-2", file_id="file-2", title="Two", metadata_revision=3),
    ]

    class FakeRepository:
        async def list_pending_reindex(self):
            return papers

        async def fail_reindex(self, **kwargs):
            failed.append(kwargs)

    calls = 0

    async def enqueue(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("queue unavailable Authorization=secret-api-key")
        return SimpleNamespace(id="task-2"), True

    async def find_previous_task(**kwargs):
        return SimpleNamespace(payload={"operator_id": "admin-1"})

    monkeypatch.setattr(research_paper_service, "AcademicPaperRepository", FakeRepository)
    monkeypatch.setattr(research_paper_service, "_enqueue_paper_reindex", enqueue)
    monkeypatch.setattr(research_paper_service.tasker, "find_task_by_payload", find_previous_task)

    assert await research_paper_service.recover_pending_paper_reindexes() == 1
    assert failed == [
        {
            "kb_id": "kb-1",
            "paper_id": "paper-1",
            "revision": 2,
            "error": "论文元数据同步恢复任务提交失败",
        }
    ]


@pytest.mark.asyncio
async def test_recover_pending_reindex_fails_closed_without_persisted_owner(monkeypatch):
    failures = []

    class FakeRepository:
        async def list_pending_reindex(self):
            return [
                SimpleNamespace(
                    kb_id="kb-1",
                    paper_id="paper-1",
                    file_id="file-1",
                    title="One",
                    metadata_revision=2,
                )
            ]

        async def fail_reindex(self, **kwargs):
            failures.append(kwargs)

    async def find_previous_task(**kwargs):
        return None

    async def reject_enqueue(**kwargs):
        raise AssertionError("ownerless recovery must not enqueue a write task")

    monkeypatch.setattr(research_paper_service, "AcademicPaperRepository", FakeRepository)
    monkeypatch.setattr(research_paper_service.tasker, "find_task_by_payload", find_previous_task)
    monkeypatch.setattr(research_paper_service, "_enqueue_paper_reindex", reject_enqueue)

    assert await research_paper_service.recover_pending_paper_reindexes() == 0
    assert failures == [
        {
            "kb_id": "kb-1",
            "paper_id": "paper-1",
            "revision": 2,
            "error": "论文元数据同步恢复任务缺少可验证的所有者",
        }
    ]


@pytest.mark.asyncio
async def test_new_reindex_task_executes_through_owner_validation(monkeypatch):
    resumed = []

    async def resume(context):
        resumed.append(context.payload)
        return {"status": "indexed"}

    async def enqueue_unique_by_payload(**kwargs):
        context = SimpleNamespace(payload=kwargs["payload"])
        result = await kwargs["coroutine"](context)
        return SimpleNamespace(id="task-1", status="success", result=result), True

    monkeypatch.setattr(research_paper_service, "_resume_paper_reindex_task", resume)
    monkeypatch.setattr(research_paper_service.tasker, "enqueue_unique_by_payload", enqueue_unique_by_payload)

    await research_paper_service._enqueue_paper_reindex(
        kb_id="kb-1",
        paper_id="paper-1",
        file_id="file-1",
        paper_title="Paper",
        operator_id="admin-1",
        revision=3,
    )

    assert resumed == [
        {
            "kb_id": "kb-1",
            "paper_id": "paper-1",
            "file_id": "file-1",
            "operator_id": "admin-1",
            "revision": 3,
        }
    ]


@pytest.mark.asyncio
async def test_resume_paper_reindex_uses_persisted_operator_and_payload(monkeypatch):
    calls = []

    class FakeUserRepository:
        async def get_by_uid(self, uid):
            assert uid == "admin-1"
            return SimpleNamespace(uid=uid, is_deleted=False)

    async def allow_access(user, kb_id, *, write=False):
        calls.append(("access", user.uid, kb_id, write))

    async def run_reindex(context, **kwargs):
        calls.append(("run", context, kwargs))
        return {"status": "indexed"}

    monkeypatch.setattr(research_paper_service, "UserRepository", FakeUserRepository)
    monkeypatch.setattr(research_paper_service, "_ensure_access", allow_access)
    monkeypatch.setattr(research_paper_service, "_run_paper_reindex", run_reindex)
    context = SimpleNamespace(
        payload={
            "kb_id": "kb-1",
            "paper_id": "paper-1",
            "file_id": "file-1",
            "operator_id": "admin-1",
            "revision": 3,
        }
    )

    result = await research_paper_service._resume_paper_reindex_task(context)

    assert result == {"status": "indexed"}
    assert calls == [
        ("access", "admin-1", "kb-1", True),
        (
            "run",
            context,
            {
                "kb_id": "kb-1",
                "paper_id": "paper-1",
                "file_id": "file-1",
                "operator_id": "admin-1",
                "target_revision": 3,
            },
        ),
    ]


@pytest.mark.asyncio
async def test_reindex_failure_does_not_persist_or_raise_provider_secret(monkeypatch):
    secret = "Authorization=secret-api-key provider-body=<private>"
    failures = []

    class FakeRepository:
        async def get_by_paper_id(self, **kwargs):
            return SimpleNamespace(metadata_revision=3, indexed_revision=2, metadata_status="pending")

        async def fail_reindex(self, **kwargs):
            failures.append(kwargs)

    class Context:
        async def raise_if_cancelled(self):
            return None

        async def set_progress(self, *args):
            return None

    @asynccontextmanager
    async def unlocked(*args):
        yield

    async def allow_owner(*args, **kwargs):
        return SimpleNamespace(uid="admin-1")

    async def fail_index(*args, **kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(research_paper_service, "AcademicPaperRepository", FakeRepository)
    monkeypatch.setattr(research_paper_service, "_paper_reindex_lock", unlocked)
    monkeypatch.setattr(research_paper_service, "_ensure_reindex_owner_can_write", allow_owner)
    monkeypatch.setattr(research_paper_service, "_serialize_paper", lambda paper: {"paper_id": "paper-1"})
    monkeypatch.setattr(research_paper_service.knowledge_base, "index_file", fail_index)

    with pytest.raises(research_paper_service.ResearchPaperReindexError) as exc_info:
        await research_paper_service._run_paper_reindex(
            Context(),
            kb_id="kb-1",
            paper_id="paper-1",
            file_id="file-1",
            operator_id="admin-1",
            target_revision=3,
        )

    assert exc_info.value.error_type == "paper_reindex_failed"
    assert exc_info.value.message == "论文元数据与检索索引同步失败"
    assert failures[-1]["error"] == "论文元数据与检索索引同步失败"
    assert secret not in repr(failures)
    assert research_paper_service.tasker._resumable_handlers["research_paper_reindex"] is (
        research_paper_service._resume_paper_reindex_task
    )


@pytest.mark.asyncio
async def test_resume_paper_reindex_marks_business_record_failed_when_owner_is_deleted(monkeypatch):
    failures = []

    class FakeUserRepository:
        async def get_by_uid(self, uid):
            return SimpleNamespace(uid=uid, is_deleted=True)

    class FakePaperRepository:
        async def fail_reindex(self, **kwargs):
            failures.append(kwargs)

    monkeypatch.setattr(research_paper_service, "UserRepository", FakeUserRepository)
    monkeypatch.setattr(research_paper_service, "AcademicPaperRepository", FakePaperRepository)
    context = SimpleNamespace(
        payload={
            "kb_id": "kb-1",
            "paper_id": "paper-1",
            "file_id": "file-1",
            "operator_id": "deleted-admin",
            "revision": 3,
        }
    )

    with pytest.raises(RuntimeError, match="所有者不存在或已删除"):
        await research_paper_service._resume_paper_reindex_task(context)

    assert failures == [
        {
            "kb_id": "kb-1",
            "paper_id": "paper-1",
            "revision": 3,
            "error": "论文元数据同步任务所有者不存在或已删除",
        }
    ]
