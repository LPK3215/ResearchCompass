"""学术图谱同步运行记录的访问控制测试。"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from yuxi.services import academic_graph_sync_service


@pytest.fixture
def denied_graph_access(monkeypatch):
    checked_kb_ids = []

    async def deny_access(current_user, kb_id):
        checked_kb_ids.append(kb_id)
        raise HTTPException(status_code=403, detail="无权访问该知识库")

    class FakeRepository:
        async def get_sync_run(self, run_id):
            return SimpleNamespace(kb_id="kb-private", uid="user-1")

    monkeypatch.setattr(academic_graph_sync_service, "_ensure_access", deny_access)
    monkeypatch.setattr(academic_graph_sync_service, "AcademicGraphRepository", FakeRepository)
    return checked_kb_ids


@pytest.mark.asyncio
async def test_get_sync_run_rechecks_knowledge_base_access(denied_graph_access):
    with pytest.raises(HTTPException) as exc_info:
        await academic_graph_sync_service.get_academic_graph_sync_run(
            run_id="run-1",
            current_user=SimpleNamespace(uid="user-1", role="user"),
        )

    assert exc_info.value.status_code == 403
    assert denied_graph_access == ["kb-private"]


@pytest.mark.asyncio
async def test_list_conflicts_rechecks_knowledge_base_access(denied_graph_access):
    with pytest.raises(HTTPException) as exc_info:
        await academic_graph_sync_service.list_academic_graph_conflicts(
            run_id="run-1",
            current_user=SimpleNamespace(uid="user-1", role="user"),
            resolution_status=None,
            offset=0,
            limit=50,
        )

    assert exc_info.value.status_code == 403
    assert denied_graph_access == ["kb-private"]


@pytest.mark.asyncio
async def test_graph_sync_recovery_skips_owner_without_write_access(monkeypatch):
    updates = []

    class FakeRepository:
        async def list_recoverable_sync_runs(self):
            return [SimpleNamespace(run_id="run-1", kb_id="kb-private", uid="user-1", sync_config={})]

        async def update_sync_run(self, run_id, values):
            updates.append((run_id, values))

    class FakeUserRepository:
        async def get_by_uid(self, uid):
            return SimpleNamespace(uid=uid, is_deleted=False, role="user", department_id=None)

    async def deny_write_access(*args, **kwargs):
        raise HTTPException(status_code=403, detail="需要管理员权限")

    async def fail_if_enqueued(**kwargs):
        raise AssertionError("inaccessible graph sync must not be re-enqueued")

    monkeypatch.setattr(academic_graph_sync_service, "AcademicGraphRepository", FakeRepository)
    monkeypatch.setattr(academic_graph_sync_service, "UserRepository", FakeUserRepository)
    monkeypatch.setattr(academic_graph_sync_service, "_ensure_access", deny_write_access)
    monkeypatch.setattr(academic_graph_sync_service.tasker, "enqueue_unique_by_payload", fail_if_enqueued)

    assert await academic_graph_sync_service.recover_academic_graph_sync_runs() == 0
    assert updates[0][0] == "run-1"
    assert updates[0][1]["error_type"] == "graph_sync_recovery_invalid"


@pytest.mark.asyncio
async def test_graph_sync_reuses_persisted_paper_checkpoint(monkeypatch):
    resolved_papers = []
    updates = []

    class FakeGraphRepository:
        async def get_sync_run(self, run_id):
            return SimpleNamespace(
                processed_paper_ids=["paper-1"],
                graph_papers=2,
                citations=1,
                authors=3,
                topics=4,
                conflict_count=0,
            )

        async def update_sync_run(self, run_id, values):
            updates.append(values)

        async def counts(self, kb_id):
            return {"papers": 3, "citations": 2, "authors": 5, "topics": 6}

    class FakePaperRepository:
        async def list_for_graph_sync(self, *, kb_id, paper_ids):
            return [
                SimpleNamespace(id=1, paper_id="paper-1", title="Completed", external_ids={}),
                SimpleNamespace(id=2, paper_id="paper-2", title="Pending", external_ids={}),
            ]

        async def update_external_metadata(self, **kwargs):
            return None

    class FakeClient:
        async def resolve_paper(self, paper):
            resolved_papers.append(paper.paper_id)
            return {"paperId": "external-2", "externalIds": {}, "citationCount": 1}

        async def list_citations(self, paper_id, limit):
            return []

        async def list_references(self, paper_id, limit):
            return []

    class Context:
        cancellation_reason = None

        async def raise_if_cancelled(self):
            return None

        async def set_progress(self, *args):
            return None

        async def set_result(self, result):
            return None

    async def persist_paper(**kwargs):
        return "graph-paper-2", 1, 1

    async def allow_owner(*args, **kwargs):
        return None

    monkeypatch.setattr(academic_graph_sync_service, "AcademicGraphRepository", FakeGraphRepository)
    monkeypatch.setattr(academic_graph_sync_service, "AcademicPaperRepository", FakePaperRepository)
    monkeypatch.setattr(academic_graph_sync_service, "SemanticScholarClient", FakeClient)
    monkeypatch.setattr(academic_graph_sync_service, "_persist_paper", persist_paper)
    monkeypatch.setattr(academic_graph_sync_service, "_ensure_graph_sync_owner_can_write", allow_owner)

    result = await academic_graph_sync_service._run_sync(
        Context(),
        run_id="run-1",
        kb_id="kb-1",
        paper_ids=["paper-1", "paper-2"],
        citation_limit=5,
        reference_limit=5,
    )

    assert resolved_papers == ["paper-2"]
    assert result["processed_papers"] == 2
    assert updates[-1]["processed_paper_ids"] == ["paper-1", "paper-2"]


@pytest.mark.asyncio
async def test_new_graph_sync_queues_owner_checked_handler(monkeypatch):
    queued = {}

    async def allow_access(*args, **kwargs):
        return None

    class FakeRepository:
        async def create_sync_run(self, **kwargs):
            return None

    async def enqueue_unique_by_payload(**kwargs):
        queued.update(kwargs)
        return SimpleNamespace(id="task-1", status="pending"), True

    monkeypatch.setattr(academic_graph_sync_service, "_ensure_access", allow_access)
    monkeypatch.setattr(academic_graph_sync_service, "AcademicGraphRepository", FakeRepository)
    monkeypatch.setattr(
        academic_graph_sync_service.tasker,
        "enqueue_unique_by_payload",
        enqueue_unique_by_payload,
    )

    result = await academic_graph_sync_service.enqueue_academic_graph_sync(
        kb_id="kb-1",
        current_user=SimpleNamespace(uid="admin-1"),
        paper_ids=[],
        citation_limit=10,
        reference_limit=10,
    )

    assert result["task_id"] == "task-1"
    assert queued["coroutine"] is academic_graph_sync_service._resume_academic_graph_sync_task


@pytest.mark.asyncio
async def test_graph_sync_resume_handler_reuses_original_run(monkeypatch):
    calls = []
    run = SimpleNamespace(
        run_id="run-1",
        kb_id="kb-1",
        uid="user-1",
        status="running",
        sync_config={"citation_limit": 10, "reference_limit": 20},
        requested_paper_ids=["paper-1"],
    )

    class FakeRepository:
        async def get_sync_run(self, run_id):
            return run

    class FakeUserRepository:
        async def get_by_uid(self, uid):
            return SimpleNamespace(uid=uid, is_deleted=False)

    async def allow_access(user, kb_id, *, write=False):
        calls.append(("access", user.uid, kb_id, write))

    async def run_sync(context, **kwargs):
        calls.append(("run", context, kwargs))
        return {"status": "success"}

    monkeypatch.setattr(academic_graph_sync_service, "AcademicGraphRepository", FakeRepository)
    monkeypatch.setattr(academic_graph_sync_service, "UserRepository", FakeUserRepository)
    monkeypatch.setattr(academic_graph_sync_service, "_ensure_access", allow_access)
    monkeypatch.setattr(academic_graph_sync_service, "_run_sync", run_sync)
    context = SimpleNamespace(payload={"run_id": "run-1"})

    result = await academic_graph_sync_service._resume_academic_graph_sync_task(context)

    assert result == {"status": "success"}
    assert calls[0] == ("access", "user-1", "kb-1", True)
    assert calls[1][2]["paper_ids"] == ["paper-1"]
    assert academic_graph_sync_service.tasker._resumable_handlers["academic_graph_sync"] is (
        academic_graph_sync_service._resume_academic_graph_sync_task
    )


@pytest.mark.asyncio
async def test_graph_sync_unknown_failure_is_persisted_and_raised_without_provider_secret(monkeypatch):
    secret = "Authorization=secret-api-key provider-body=<private>"
    updates = []

    class FakeGraphRepository:
        async def get_sync_run(self, run_id):
            return SimpleNamespace(
                processed_paper_ids=[],
                graph_papers=0,
                citations=0,
                authors=0,
                topics=0,
                conflict_count=0,
            )

        async def update_sync_run(self, run_id, values):
            updates.append(values)

    class FakePaperRepository:
        async def list_for_graph_sync(self, *, kb_id, paper_ids):
            raise RuntimeError(secret)

    class Context:
        cancellation_reason = None

        async def raise_if_cancelled(self):
            return None

        async def set_progress(self, *args):
            return None

    async def allow_owner(*args, **kwargs):
        return None

    monkeypatch.setattr(academic_graph_sync_service, "AcademicGraphRepository", FakeGraphRepository)
    monkeypatch.setattr(academic_graph_sync_service, "AcademicPaperRepository", FakePaperRepository)
    monkeypatch.setattr(academic_graph_sync_service, "_ensure_graph_sync_owner_can_write", allow_owner)

    with pytest.raises(academic_graph_sync_service.AcademicGraphSyncError) as exc_info:
        await academic_graph_sync_service._run_sync(
            Context(),
            run_id="run-1",
            kb_id="kb-1",
            paper_ids=[],
            citation_limit=5,
            reference_limit=5,
        )

    assert exc_info.value.error_type == "academic_graph_sync_failed"
    assert exc_info.value.message == "学术图谱同步任务执行失败"
    assert secret not in repr(updates)
