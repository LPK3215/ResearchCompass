"""证据约束研究综述的关键验证规则测试。"""

import asyncio
from io import BytesIO
from types import SimpleNamespace

import pytest
from docx import Document

from yuxi.services import research_synthesis_service


def _snapshot() -> dict:
    return {
        "search_run_id": "search-1",
        "rewritten_query": "evidence synthesis",
        "keywords": ["evidence"],
        "papers": [
            {
                "paper_id": "paper-1",
                "title": "Paper One",
                "authors": ["Author One"],
                "publication_year": 2024,
                "venue": "Venue One",
                "doi": "10.1/one",
                "evidence": [
                    {
                        "chunk_id": "chunk-1",
                        "file_id": "file-1",
                        "content": "Paper one reports a reproducible result.",
                        "section_type": "results",
                        "section_title": "Results",
                        "locator": {"page": 4},
                    }
                ],
            },
            {
                "paper_id": "paper-2",
                "title": "Paper Two",
                "authors": ["Author Two"],
                "publication_year": 2025,
                "venue": "Venue Two",
                "doi": "10.1/two",
                "evidence": [
                    {
                        "chunk_id": "chunk-2",
                        "file_id": "file-2",
                        "content": "Paper two independently confirms the result.",
                        "section_type": "results",
                        "section_title": "Results",
                        "locator": {"page": 7},
                    }
                ],
            },
        ],
    }


def _evidence_index() -> dict:
    return {
        "chunk-1": {
            "chunk_id": "chunk-1",
            "paper_id": "paper-1",
            "paper_title": "Paper One",
            "section_type": "results",
            "section_title": "Results",
            "locator": {"page": 4},
        },
        "chunk-2": {
            "chunk_id": "chunk-2",
            "paper_id": "paper-2",
            "paper_title": "Paper Two",
            "section_type": "results",
            "section_title": "Results",
            "locator": {"page": 7},
        },
    }


def _report_payload(*, evidence_chunk_ids: list[str], confidence: str = "medium") -> dict:
    return {
        "executive_summary": {"text": "The result is evidence-grounded.", "claim_ids": ["C1"]},
        "themes": [{"title": "Evidence", "summary": "Independent support exists.", "claim_ids": ["C1"]}],
        "claims": [
            {
                "claim_id": "C1",
                "statement": "The approach has reproducible evidence.",
                "confidence": confidence,
                "evidence_chunk_ids": evidence_chunk_ids,
            }
        ],
        "contradictions": [],
        "limitations": [],
        "research_gaps": [],
        "unsupported_claims": [],
    }


def _validated_report() -> dict:
    return research_synthesis_service._validate_report(
        _report_payload(evidence_chunk_ids=["chunk-1", "chunk-2"], confidence="high"),
        query="What evidence supports the approach?",
        snapshot=_snapshot(),
        evidence_index=_evidence_index(),
    )


def test_validate_report_rejects_unknown_chunk_citation():
    with pytest.raises(research_synthesis_service.ResearchSynthesisError) as exc_info:
        research_synthesis_service._validate_report(
            _report_payload(evidence_chunk_ids=["unknown-chunk"]),
            query="What evidence supports the approach?",
            snapshot=_snapshot(),
            evidence_index=_evidence_index(),
        )

    assert exc_info.value.error_type == "synthesis_invalid_citation"


def test_validate_report_downgrades_single_paper_high_confidence():
    result = research_synthesis_service._validate_report(
        _report_payload(evidence_chunk_ids=["chunk-1"], confidence="high"),
        query="What evidence supports the approach?",
        snapshot=_snapshot(),
        evidence_index=_evidence_index(),
    )

    assert result["claims"][0]["confidence"] == "medium"
    assert result["validation"]["confidence_adjustments"] == [
        {"claim_id": "C1", "reason": "高置信结论只有一篇论文支持，已按验证规则降为 medium"}
    ]


def test_validate_report_moves_claim_without_evidence_to_unsupported():
    payload = _report_payload(evidence_chunk_ids=[])
    payload["executive_summary"]["claim_ids"] = []
    payload["themes"] = []

    result = research_synthesis_service._validate_report(
        payload,
        query="What evidence supports the approach?",
        snapshot=_snapshot(),
        evidence_index=_evidence_index(),
    )

    assert result["claims"] == []
    assert result["unsupported_claims"] == [
        {"statement": "The approach has reproducible evidence.", "reason": "模型未提供可验证证据"}
    ]
    assert result["coverage"]["citation_coverage_ratio"] == 0


def test_validate_report_requires_two_papers_for_contradictions():
    payload = _report_payload(evidence_chunk_ids=["chunk-1"])
    payload["contradictions"] = [
        {
            "statement": "The reported effect is contradictory.",
            "evidence_chunk_ids": ["chunk-1"],
        }
    ]

    result = research_synthesis_service._validate_report(
        payload,
        query="What evidence supports the approach?",
        snapshot=_snapshot(),
        evidence_index=_evidence_index(),
    )

    assert result["contradictions"] == []
    assert result["unsupported_claims"] == [
        {"statement": "The reported effect is contradictory.", "reason": "矛盾判断不足两篇不同论文证据"}
    ]


@pytest.mark.asyncio
async def test_verified_evidence_index_rejects_changed_evidence(monkeypatch):
    class FakeChunkRepository:
        async def list_by_chunk_ids(self, chunk_ids):
            return [
                SimpleNamespace(
                    chunk_id="chunk-1",
                    file_id="file-1",
                    kb_id="kb-1",
                    content="The source chunk was modified after retrieval.",
                ),
                SimpleNamespace(
                    chunk_id="chunk-2",
                    file_id="file-2",
                    kb_id="kb-1",
                    content="Paper two independently confirms the result.",
                ),
            ]

    class FakePaperRepository:
        async def list_by_file_ids(self, *, kb_id, file_ids):
            return [
                SimpleNamespace(file_id="file-1", paper_id="paper-1", title="Paper One"),
                SimpleNamespace(file_id="file-2", paper_id="paper-2", title="Paper Two"),
            ]

    monkeypatch.setattr(research_synthesis_service, "KnowledgeChunkRepository", FakeChunkRepository)
    monkeypatch.setattr(research_synthesis_service, "AcademicPaperRepository", FakePaperRepository)

    with pytest.raises(research_synthesis_service.ResearchSynthesisError) as exc_info:
        await research_synthesis_service._verified_evidence_index("kb-1", _snapshot())

    assert exc_info.value.error_type == "synthesis_evidence_changed"


def test_synthesis_context_budget_fails_without_silent_truncation():
    model = SimpleNamespace(model=SimpleNamespace(profile={"max_input_tokens": 1}), info={})

    with pytest.raises(research_synthesis_service.ResearchSynthesisError) as exc_info:
        research_synthesis_service._ensure_context_budget(model, "A short context is still too large for one token.")

    assert exc_info.value.error_type == "synthesis_context_exceeded"


def test_synthesis_export_renderers_include_verified_evidence():
    result = _validated_report()

    markdown = research_synthesis_service._render_markdown(result)
    document = Document(BytesIO(research_synthesis_service._render_docx(result)))
    docx_text = "\n".join(paragraph.text for paragraph in document.paragraphs)

    assert "[1] Paper One" in markdown
    assert "chunk_id=chunk-2" in markdown
    assert "证据约束研究综述" in docx_text
    assert "The approach has reproducible evidence." in docx_text


@pytest.mark.asyncio
async def test_recovery_keeps_each_run_model_and_retrieval_configuration(monkeypatch):
    records = [
        SimpleNamespace(
            run_id="run-1",
            kb_id="kb-1",
            uid="user-1",
            raw_query="question one",
            model_config_json={"model": "chat-one", "reranker_model": "rerank-one"},
            retrieval_config={"top_k": 2, "recall_top_k": 20},
        ),
        SimpleNamespace(
            run_id="run-2",
            kb_id="kb-2",
            uid="user-2",
            raw_query="question two",
            model_config_json={"model": "chat-two", "reranker_model": "rerank-two"},
            retrieval_config={"top_k": 3, "recall_top_k": 30},
        ),
    ]
    scheduled = []
    recovered = []

    class FakeRepository:
        async def list_recoverable(self):
            return records

        async def update(self, run_id, values):
            recovered.append((run_id, values))

    class FakeUserRepository:
        async def get_by_uid(self, uid):
            return SimpleNamespace(uid=uid, is_deleted=False, role="user", department_id=None)

    async def fake_enqueue_unique_by_payload(*, coroutine, **kwargs):
        scheduled.append(coroutine)
        return SimpleNamespace(id=f"task-{len(scheduled)}"), True

    async def fake_run_synthesis(context, **kwargs):
        recovered.append((kwargs["run_id"], kwargs))
        return kwargs

    monkeypatch.setattr(research_synthesis_service, "ResearchSynthesisRepository", FakeRepository)
    monkeypatch.setattr(research_synthesis_service, "UserRepository", FakeUserRepository)
    monkeypatch.setattr(research_synthesis_service, "_ensure_access", lambda *args, **kwargs: asyncio.sleep(0))
    monkeypatch.setattr(research_synthesis_service.tasker, "enqueue_unique_by_payload", fake_enqueue_unique_by_payload)
    monkeypatch.setattr(research_synthesis_service, "_run_synthesis", fake_run_synthesis)

    assert await research_synthesis_service.recover_research_synthesis_runs() == 2
    for coroutine in scheduled:
        await coroutine(SimpleNamespace())

    resumed = {entry[0]: entry[1] for entry in recovered if len(entry) == 2 and isinstance(entry[1], dict)}
    assert resumed["run-1"]["model_spec"] == "chat-one"
    assert resumed["run-1"]["retrieval_config"]["top_k"] == 2
    assert resumed["run-2"]["model_spec"] == "chat-two"
    assert resumed["run-2"]["retrieval_config"]["top_k"] == 3


@pytest.mark.asyncio
async def test_resume_synthesis_reuses_persisted_snapshot_without_search(monkeypatch):
    updates = []

    class FakeRepository:
        async def update_if_not_cancelled(self, run_id, values):
            updates.append(values)
            return SimpleNamespace()

    class Context:
        cancellation_reason = None

        async def raise_if_cancelled(self):
            return None

        async def set_progress(self, *args):
            return None

        async def set_result(self, result):
            return None

    async def unexpected_search(**kwargs):
        pytest.fail("a persisted retrieval snapshot must be reused after restart")

    monkeypatch.setattr(research_synthesis_service, "ResearchSynthesisRepository", FakeRepository)
    monkeypatch.setattr(research_synthesis_service, "search_papers", unexpected_search)
    monkeypatch.setattr(
        research_synthesis_service,
        "_call_synthesis_model",
        lambda *args: asyncio.sleep(
            0, result=_report_payload(evidence_chunk_ids=["chunk-1", "chunk-2"], confidence="high")
        ),
    )
    monkeypatch.setattr(
        research_synthesis_service,
        "_verified_evidence_index",
        lambda *args: asyncio.sleep(0, result=_evidence_index()),
    )

    result = await research_synthesis_service._run_synthesis(
        Context(),
        run_id="run-1",
        kb_id="kb-1",
        current_user=SimpleNamespace(),
        query="What evidence supports the approach?",
        model_spec="chat:model",
        reranker_model="rerank:model",
        retrieval_config={"top_k": 2, "recall_top_k": 20},
        persisted_snapshot=_snapshot(),
        resume_stage_timings={"retrieval_ms": 12},
    )

    assert result["validation"]["status"] == "verified"
    assert [values["status"] for values in updates] == ["synthesizing", "validating", "success"]
    assert updates[-1]["stage_timings"]["retrieval_ms"] == 12


@pytest.mark.asyncio
async def test_resume_synthesis_handler_reuses_original_run_and_snapshot(monkeypatch):
    calls = []
    record = SimpleNamespace(
        run_id="run-1",
        kb_id="kb-1",
        uid="user-1",
        raw_query="question",
        status="synthesizing",
        result=None,
        model_config_json={"model": "chat-one", "reranker_model": "rerank-one"},
        retrieval_config={"top_k": 2, "recall_top_k": 20},
        retrieval_snapshot=_snapshot(),
        stage_timings={"retrieval_ms": 15},
    )

    class FakeRepository:
        async def get(self, run_id):
            assert run_id == "run-1"
            return record

    class FakeUserRepository:
        async def get_by_uid(self, uid):
            return SimpleNamespace(uid=uid, is_deleted=False)

    async def allow_access(user, kb_id):
        calls.append(("access", user.uid, kb_id))

    async def run_synthesis(context, **kwargs):
        calls.append(("run", context, kwargs))
        return {"status": "success"}

    monkeypatch.setattr(research_synthesis_service, "ResearchSynthesisRepository", FakeRepository)
    monkeypatch.setattr(research_synthesis_service, "UserRepository", FakeUserRepository)
    monkeypatch.setattr(research_synthesis_service, "_ensure_access", allow_access)
    monkeypatch.setattr(research_synthesis_service, "_run_synthesis", run_synthesis)
    context = SimpleNamespace(payload={"run_id": "run-1"})

    result = await research_synthesis_service._resume_research_synthesis_task(context)

    assert result == {"status": "success"}
    assert calls[0] == ("access", "user-1", "kb-1")
    assert calls[1][2]["run_id"] == "run-1"
    assert calls[1][2]["persisted_snapshot"] == _snapshot()
    assert calls[1][2]["resume_stage_timings"] == {"retrieval_ms": 15}
    assert research_synthesis_service.tasker._resumable_handlers["research_synthesis"] is (
        research_synthesis_service._resume_research_synthesis_task
    )


@pytest.mark.asyncio
async def test_shutdown_cancellation_keeps_synthesis_recoverable(monkeypatch):
    updates = []

    class FakeRepository:
        async def update(self, run_id, values):
            updates.append((run_id, values))

        async def update_if_not_cancelled(self, run_id, values):
            updates.append((run_id, values))
            return SimpleNamespace()

    class ShutdownContext:
        cancellation_reason = "shutdown"

        async def raise_if_cancelled(self):
            raise asyncio.CancelledError()

    monkeypatch.setattr(research_synthesis_service, "ResearchSynthesisRepository", FakeRepository)

    with pytest.raises(asyncio.CancelledError):
        await research_synthesis_service._run_synthesis(
            ShutdownContext(),
            run_id="run-1",
            kb_id="kb-1",
            current_user=SimpleNamespace(),
            query="question",
            model_spec="chat:model",
            reranker_model="rerank:model",
            retrieval_config={"top_k": 2, "recall_top_k": 20},
        )

    assert updates[-1] == (
        "run-1",
        {
            "status": "pending",
            "stage": "pending",
            "stage_timings": {},
            "error_type": "synthesis_recovery_pending",
            "error_message": "服务重启，综述任务等待自动恢复",
            "completed_at": None,
        },
    )


@pytest.mark.asyncio
async def test_cancelled_run_cannot_be_overwritten_by_a_late_stage_write(monkeypatch):
    updates = []

    class FakeRepository:
        async def update_if_not_cancelled(self, run_id, values):
            updates.append(("conditional", run_id, values))
            if values.get("status") == "synthesizing":
                self.cancelled = True
                return None
            if getattr(self, "cancelled", False):
                return None
            return SimpleNamespace()

    class Context:
        cancellation_reason = None

        async def raise_if_cancelled(self):
            return None

        async def set_progress(self, *args):
            return None

        async def set_result(self, result):
            raise AssertionError("Cancelled synthesis must not publish a result")

    monkeypatch.setattr(research_synthesis_service, "ResearchSynthesisRepository", FakeRepository)
    monkeypatch.setattr(research_synthesis_service, "search_papers", lambda **kwargs: asyncio.sleep(0, result={}))
    monkeypatch.setattr(research_synthesis_service, "_build_snapshot", lambda result: {"papers": [{}, {}]})
    async def unexpected_model_call(*args):
        raise AssertionError("Cancelled synthesis must stop before invoking the model")

    monkeypatch.setattr(research_synthesis_service, "_call_synthesis_model", unexpected_model_call)

    with pytest.raises(asyncio.CancelledError):
        await research_synthesis_service._run_synthesis(
            Context(),
            run_id="run-1",
            kb_id="kb-1",
            current_user=SimpleNamespace(),
            query="question",
            model_spec="chat:model",
            reranker_model="rerank:model",
            retrieval_config={"top_k": 2, "recall_top_k": 20},
        )

    statuses = [entry[2].get("status") for entry in updates]
    assert statuses == ["retrieving", "synthesizing", "cancelled"]


@pytest.mark.asyncio
async def test_cancel_synthesis_accepts_worker_winning_the_terminal_transition(monkeypatch):
    record = SimpleNamespace(
        run_id="run-1",
        kb_id="kb-1",
        uid="user-1",
        task_id="task-1",
        status="retrieving",
    )

    class FakeRepository:
        async def get(self, run_id):
            return record

        async def mark_cancelled(self, run_id, *, completed_at):
            record.status = "cancelled"
            return None

        @staticmethod
        def serialize(item):
            return {"run_id": item.run_id, "status": item.status, "task_id": item.task_id}

    async def cancel_task(task_id):
        return True

    monkeypatch.setattr(research_synthesis_service, "ResearchSynthesisRepository", FakeRepository)
    monkeypatch.setattr(research_synthesis_service, "_ensure_access", lambda *args, **kwargs: asyncio.sleep(0))
    monkeypatch.setattr(research_synthesis_service.tasker, "cancel_task", cancel_task)

    result = await research_synthesis_service.cancel_research_synthesis(
        run_id="run-1",
        current_user=SimpleNamespace(uid="user-1", role="user"),
    )

    assert result == {"run_id": "run-1", "status": "cancelled", "task_id": "task-1"}


@pytest.mark.asyncio
async def test_cancel_synthesis_requests_task_cancellation_before_marking_run_terminal(monkeypatch):
    record = SimpleNamespace(
        run_id="run-1",
        kb_id="kb-1",
        uid="user-1",
        task_id="task-1",
        status="retrieving",
    )
    calls = []

    class FakeRepository:
        async def get(self, run_id):
            return record

        async def mark_cancelled(self, run_id, *, completed_at):
            calls.append(("mark", run_id))
            record.status = "cancelled"
            return record

        @staticmethod
        def serialize(item):
            return {"run_id": item.run_id, "status": item.status, "task_id": item.task_id}

    async def cancel_task(task_id):
        calls.append(("task", task_id))
        return True

    monkeypatch.setattr(research_synthesis_service, "ResearchSynthesisRepository", FakeRepository)
    monkeypatch.setattr(research_synthesis_service, "_ensure_access", lambda *args, **kwargs: asyncio.sleep(0))
    monkeypatch.setattr(research_synthesis_service.tasker, "cancel_task", cancel_task)

    result = await research_synthesis_service.cancel_research_synthesis(
        run_id="run-1",
        current_user=SimpleNamespace(uid="user-1", role="user"),
    )

    assert result == {"run_id": "run-1", "status": "cancelled", "task_id": "task-1"}
    assert calls == [("task", "task-1"), ("mark", "run-1")]
