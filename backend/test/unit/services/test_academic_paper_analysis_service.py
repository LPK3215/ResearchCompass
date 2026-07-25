"""论文分析运行记录的访问控制测试。"""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from yuxi.services import academic_paper_analysis_service


@pytest.mark.asyncio
async def test_get_analysis_run_rechecks_knowledge_base_access(monkeypatch):
    checked_kb_ids = []

    async def deny_access(current_user, kb_id):
        checked_kb_ids.append(kb_id)
        raise HTTPException(status_code=403, detail="无权访问该知识库")

    class FakeRepository:
        async def get(self, run_id):
            return SimpleNamespace(kb_id="kb-private", uid="user-1")

    monkeypatch.setattr(academic_paper_analysis_service, "_ensure_access", deny_access)
    monkeypatch.setattr(academic_paper_analysis_service, "AcademicPaperAnalysisRepository", FakeRepository)

    with pytest.raises(HTTPException) as exc_info:
        await academic_paper_analysis_service.get_paper_analysis_run(
            run_id="run-1",
            current_user=SimpleNamespace(uid="user-1", role="user"),
        )

    assert exc_info.value.status_code == 403
    assert checked_kb_ids == ["kb-private"]


@pytest.mark.asyncio
async def test_get_latest_analysis_filters_by_current_user(monkeypatch):
    requested = {}

    async def allow_access(current_user, kb_id):
        return None

    class FakePaperRepository:
        async def get_by_paper_id(self, *, kb_id, paper_id):
            return SimpleNamespace(id=42)

    class FakeAnalysisRepository:
        async def get_latest(self, **kwargs):
            requested.update(kwargs)
            return None

    monkeypatch.setattr(academic_paper_analysis_service, "_ensure_access", allow_access)
    monkeypatch.setattr(academic_paper_analysis_service, "AcademicPaperRepository", FakePaperRepository)
    monkeypatch.setattr(academic_paper_analysis_service, "AcademicPaperAnalysisRepository", FakeAnalysisRepository)

    result = await academic_paper_analysis_service.get_latest_paper_analysis(
        kb_id="kb-shared",
        paper_id="paper-1",
        current_user=SimpleNamespace(uid="current-user", role="user"),
    )

    assert result is None
    assert requested == {
        "kb_id": "kb-shared",
        "academic_paper_id": 42,
        "uid": "current-user",
    }


@pytest.mark.asyncio
async def test_analysis_recovery_skips_owner_without_knowledge_base_access(monkeypatch):
    updates = []

    class FakeRepository:
        async def list_recoverable(self):
            return [
                SimpleNamespace(
                    run_id="run-1",
                    kb_id="kb-private",
                    uid="user-1",
                    model_config_json={"model": "chat:model"},
                    strategy="multi_agent",
                )
            ]

        async def update(self, run_id, values):
            updates.append((run_id, values))

    class FakeUserRepository:
        async def get_by_uid(self, uid):
            return SimpleNamespace(uid=uid, is_deleted=False, role="user", department_id=None)

    async def deny_access(*args, **kwargs):
        raise HTTPException(status_code=403, detail="无权访问该知识库")

    async def fail_if_enqueued(**kwargs):
        raise AssertionError("inaccessible analysis must not be re-enqueued")

    monkeypatch.setattr(academic_paper_analysis_service, "AcademicPaperAnalysisRepository", FakeRepository)
    monkeypatch.setattr(academic_paper_analysis_service, "UserRepository", FakeUserRepository)
    monkeypatch.setattr(academic_paper_analysis_service, "_ensure_access", deny_access)
    monkeypatch.setattr(academic_paper_analysis_service.tasker, "enqueue_unique_by_payload", fail_if_enqueued)

    assert await academic_paper_analysis_service.recover_paper_analysis_runs() == 0
    assert updates[0][0] == "run-1"
    assert updates[0][1]["error_type"] == "analysis_recovery_invalid"


@pytest.mark.asyncio
async def test_resume_analysis_skips_persisted_multi_agent_stages(monkeypatch):
    updates = []
    stage_results = {
        "structure": {"title": "Paper"},
        "innovations": {"items": [{"claim": "Novel"}]},
        "methodology": {"research_design": "Experiment"},
        "gaps": {"gaps": [{"claim": "Open question"}]},
    }

    class FakeRepository:
        async def update(self, run_id, values):
            updates.append(values)

    class Context:
        cancellation_reason = None

        async def raise_if_cancelled(self):
            return None

        async def set_progress(self, *args):
            return None

        async def set_result(self, result):
            return None

    async def unexpected_stage_call(*args):
        pytest.fail("completed stages must not call the model again after restart")

    monkeypatch.setattr(academic_paper_analysis_service, "AcademicPaperAnalysisRepository", FakeRepository)
    monkeypatch.setattr(
        academic_paper_analysis_service,
        "_paper_context",
        lambda *args: asyncio.sleep(0, result=(SimpleNamespace(), "paper context")),
    )
    monkeypatch.setattr(
        academic_paper_analysis_service,
        "_citation_graph_context",
        lambda *args: asyncio.sleep(0, result="graph context"),
    )
    monkeypatch.setattr(academic_paper_analysis_service, "_call_stage", unexpected_stage_call)

    result = await academic_paper_analysis_service._run_analysis(
        Context(),
        run_id="run-1",
        kb_id="kb-1",
        paper_id="paper-1",
        model_spec="chat:model",
        strategy="multi_agent",
        persisted_stage_results=stage_results,
    )

    assert result == {
        "paper_id": "paper-1",
        "model": "chat:model",
        "strategy": "multi_agent",
        "structure": stage_results["structure"],
        "innovations": stage_results["innovations"],
        "methodology": stage_results["methodology"],
        "research_gaps": stage_results["gaps"],
    }
    assert updates[-1]["status"] == "success"


@pytest.mark.asyncio
async def test_resume_analysis_handler_uses_original_run_stage_checkpoint(monkeypatch):
    calls = []
    record = SimpleNamespace(
        run_id="run-1",
        kb_id="kb-1",
        uid="user-1",
        status="running",
        result=None,
        model_config_json={"model": "chat:model"},
        strategy="multi_agent",
        academic_paper_id=42,
        stage_results={"structure": {"title": "Paper"}},
    )

    class FakeAnalysisRepository:
        async def get(self, run_id):
            assert run_id == "run-1"
            return record

    class FakeUserRepository:
        async def get_by_uid(self, uid):
            return SimpleNamespace(uid=uid, is_deleted=False)

    class FakePaperRepository:
        async def get_by_id(self, paper_id):
            assert paper_id == 42
            return SimpleNamespace(paper_id="paper-1")

    async def allow_access(user, kb_id):
        calls.append(("access", user.uid, kb_id))

    async def run_analysis(context, **kwargs):
        calls.append(("run", context, kwargs))
        return {"status": "success"}

    monkeypatch.setattr(academic_paper_analysis_service, "AcademicPaperAnalysisRepository", FakeAnalysisRepository)
    monkeypatch.setattr(academic_paper_analysis_service, "UserRepository", FakeUserRepository)
    monkeypatch.setattr(academic_paper_analysis_service, "AcademicPaperRepository", FakePaperRepository)
    monkeypatch.setattr(academic_paper_analysis_service, "_ensure_access", allow_access)
    monkeypatch.setattr(academic_paper_analysis_service, "_run_analysis", run_analysis)
    context = SimpleNamespace(payload={"run_id": "run-1"})

    result = await academic_paper_analysis_service._resume_paper_analysis_task(context)

    assert result == {"status": "success"}
    assert calls[0] == ("access", "user-1", "kb-1")
    assert calls[1][2]["persisted_stage_results"] == record.stage_results
    assert academic_paper_analysis_service.tasker._resumable_handlers["academic_paper_analysis"] is (
        academic_paper_analysis_service._resume_paper_analysis_task
    )
