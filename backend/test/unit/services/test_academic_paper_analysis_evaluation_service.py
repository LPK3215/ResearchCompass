from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from yuxi.services import academic_paper_analysis_evaluation_service


pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


async def test_create_evaluation_queues_resumable_owner_checked_handler(monkeypatch):
    queued = {}

    class FakeRepository:
        async def create_with_items(self, evaluation, items):
            return None

        async def update_evaluation(self, evaluation_id, values):
            return None

    class FakePaperRepository:
        async def get_by_paper_id(self, *, kb_id, paper_id):
            return SimpleNamespace(id=len(paper_id), paper_id=paper_id, file_id=f"file-{paper_id}")

    class FakeChunkRepository:
        async def list_academic_by_file_id(self, **kwargs):
            return [SimpleNamespace(chunk_id="chunk-1")], 1

    async def allow_access(*args, **kwargs):
        return None

    async def enqueue_unique_by_payload(**kwargs):
        queued.update(kwargs)
        return SimpleNamespace(id="task-1"), True

    monkeypatch.setattr(academic_paper_analysis_evaluation_service, "_ensure_access", allow_access)
    monkeypatch.setattr(
        academic_paper_analysis_evaluation_service.model_cache,
        "get_model_info",
        lambda model: SimpleNamespace(model_type="chat", api_key="configured", provider_type="test"),
    )
    monkeypatch.setattr(
        academic_paper_analysis_evaluation_service.tasker,
        "enqueue_unique_by_payload",
        enqueue_unique_by_payload,
    )
    service = academic_paper_analysis_evaluation_service.AcademicPaperAnalysisEvaluationService()
    service.repo = FakeRepository()
    service.paper_repo = FakePaperRepository()
    service.chunk_repo = FakeChunkRepository()

    result = await service.create_evaluation(
        kb_id="kb-1",
        current_user=SimpleNamespace(uid="admin-1"),
        name="Comparison",
        description="",
        paper_ids=["paper-1", "paper-2"],
        model_spec="chat:model",
    )

    assert result["task_id"] == "task-1"
    assert queued["coroutine"] is (academic_paper_analysis_evaluation_service._resume_paper_analysis_evaluation_task)
    assert (
        academic_paper_analysis_evaluation_service.tasker._resumable_handlers["academic_paper_analysis_evaluation"]
        is academic_paper_analysis_evaluation_service._resume_paper_analysis_evaluation_task
    )


async def test_recovery_fails_closed_when_owner_lost_write_access(monkeypatch):
    updates = []
    evaluation = SimpleNamespace(
        evaluation_id="evaluation-1",
        kb_id="kb-private",
        name="Comparison",
        created_by="admin-1",
    )

    class FakeRepository:
        async def list_recoverable(self):
            return [evaluation]

        async def update_evaluation(self, evaluation_id, values):
            updates.append((evaluation_id, values))

    class FakeUserRepository:
        async def get_by_uid(self, uid):
            return SimpleNamespace(uid=uid, is_deleted=False)

    async def deny_access(*args, **kwargs):
        raise HTTPException(status_code=403, detail="forbidden")

    async def reject_enqueue(**kwargs):
        raise AssertionError("unauthorized evaluation must not be enqueued")

    monkeypatch.setattr(
        academic_paper_analysis_evaluation_service,
        "UserRepository",
        FakeUserRepository,
    )
    monkeypatch.setattr(academic_paper_analysis_evaluation_service, "_ensure_access", deny_access)
    monkeypatch.setattr(
        academic_paper_analysis_evaluation_service.tasker,
        "enqueue_unique_by_payload",
        reject_enqueue,
    )
    service = academic_paper_analysis_evaluation_service.AcademicPaperAnalysisEvaluationService()
    service.repo = FakeRepository()

    assert await service.recover_evaluations() == 0
    assert updates[0][0] == "evaluation-1"
    assert updates[0][1]["status"] == "failed"
    assert "失去知识库写入权限" in updates[0][1]["error_message"]


async def test_create_evaluation_does_not_persist_enqueue_provider_secret(monkeypatch):
    secret = "Authorization=secret-api-key provider-body=<private>"
    updates = []

    class FakeRepository:
        async def create_with_items(self, evaluation, items):
            return None

        async def update_evaluation(self, evaluation_id, values):
            updates.append(values)

    class FakePaperRepository:
        async def get_by_paper_id(self, *, kb_id, paper_id):
            return SimpleNamespace(id=len(paper_id), paper_id=paper_id, file_id=f"file-{paper_id}")

    class FakeChunkRepository:
        async def list_academic_by_file_id(self, **kwargs):
            return [SimpleNamespace(chunk_id="chunk-1")], 1

    async def allow_access(*args, **kwargs):
        return None

    async def fail_enqueue(**kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(academic_paper_analysis_evaluation_service, "_ensure_access", allow_access)
    monkeypatch.setattr(
        academic_paper_analysis_evaluation_service.model_cache,
        "get_model_info",
        lambda model: SimpleNamespace(model_type="chat", api_key="configured", provider_type="test"),
    )
    monkeypatch.setattr(
        academic_paper_analysis_evaluation_service.tasker,
        "enqueue_unique_by_payload",
        fail_enqueue,
    )
    service = academic_paper_analysis_evaluation_service.AcademicPaperAnalysisEvaluationService()
    service.repo = FakeRepository()
    service.paper_repo = FakePaperRepository()
    service.chunk_repo = FakeChunkRepository()

    with pytest.raises(
        academic_paper_analysis_evaluation_service.AcademicPaperAnalysisEvaluationError,
        match="分析对比任务提交失败",
    ):
        await service.create_evaluation(
            kb_id="kb-1",
            current_user=SimpleNamespace(uid="admin-1"),
            name="Comparison",
            description="",
            paper_ids=["paper-1", "paper-2"],
            model_spec="chat:model",
        )

    assert updates[-1]["error_message"] == "分析对比任务提交失败"
    assert secret not in repr(updates)


async def test_evaluation_item_failure_does_not_persist_provider_secret(monkeypatch):
    secret = "Authorization=secret-api-key provider-body=<private>"
    item_updates = []
    evaluation_updates = []
    item = SimpleNamespace(item_id="item-1", academic_paper_id=1, status="pending")
    evaluation = SimpleNamespace(evaluation_id="evaluation-1", kb_id="kb-1", created_by="admin-1")

    class FakeRepository:
        async def get(self, evaluation_id):
            return evaluation

        async def update_evaluation(self, evaluation_id, values):
            evaluation_updates.append(values)

        async def list_items(self, evaluation_id):
            return [item]

        async def get_paper(self, paper_id):
            return SimpleNamespace(id=paper_id, paper_id="paper-1")

        async def update_item(self, item_id, values):
            item_updates.append(values)
            if "status" in values:
                item.status = values["status"]

    class Context:
        cancellation_reason = None

        async def raise_if_cancelled(self):
            return None

        async def set_result(self, result):
            return None

        async def set_progress(self, *args):
            return None

    async def allow_owner(*args, **kwargs):
        return SimpleNamespace(uid="admin-1")

    async def fail_pair(**kwargs):
        raise RuntimeError(secret)

    service = academic_paper_analysis_evaluation_service.AcademicPaperAnalysisEvaluationService()
    service.repo = FakeRepository()
    monkeypatch.setattr(service, "_ensure_owner_can_write", allow_owner)
    monkeypatch.setattr(service, "_ensure_pair_run", fail_pair)

    result = await service._run_evaluation(Context(), evaluation_id="evaluation-1")

    assert result == {"evaluation_id": "evaluation-1", "completed_pairs": 0}
    assert item_updates[-1]["error_message"] == "论文分析对比项执行失败"
    assert evaluation_updates[-1]["status"] == "completed_with_failures"
    assert secret not in repr((item_updates, evaluation_updates))
