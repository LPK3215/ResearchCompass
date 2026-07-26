"""科研检索运行记录的访问控制测试。"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from yuxi.services import research_search_service


def test_search_mode_validation_preserves_strict_default():
    assert (
        research_search_service._normalize_search_mode(None)
        == research_search_service.STRICT_HYBRID_GRAPH_MODE
    )
    assert (
        research_search_service._normalize_search_mode(research_search_service.LOCAL_HYBRID_MODE)
        == research_search_service.LOCAL_HYBRID_MODE
    )
    with pytest.raises(research_search_service.ResearchSearchError) as exc_info:
        research_search_service._normalize_search_mode("automatic_fallback")
    assert exc_info.value.error_type == "invalid_retrieval_config"


@pytest.mark.asyncio
async def test_local_hybrid_search_skips_citation_graph(monkeypatch):
    created_run = {}
    query_options = {}
    run_updates = []

    class FakeRunRepository:
        async def create(self, **values):
            created_run.update(values)

        async def update(self, run_id, values):
            run_updates.append(values)
            return None

    class FakeKnowledgeBaseRepository:
        async def get_by_kb_id(self, kb_id):
            return SimpleNamespace(kb_type="milvus", llm_model_spec="chat:model")

    class FakeAcademicPaperRepository:
        async def list_file_ids_by_filters(self, **kwargs):
            return ["file-1"]

        async def list_by_file_ids(self, **kwargs):
            return []

    class FakeKnowledgeBase:
        async def check_accessible(self, user_info, kb_id):
            return True

        async def aquery(self, query, kb_id, **kwargs):
            query_options.update(kwargs)
            return [
                {
                    "content": "Evidence grounded local retrieval.",
                    "hybrid_score": 0.8,
                    "rerank_score": 0.9,
                    "metadata": {
                        "paper_id": "paper-1",
                        "chunk_id": "chunk-1",
                        "file_id": "file-1",
                        "chunk_index": 0,
                    },
                }
            ]

    async def fake_rewrite(query, model_spec):
        return {"rewritten_query": "grounded retrieval", "keywords": ["retrieval"]}

    async def reject_graph_call(*args, **kwargs):
        raise AssertionError("local_hybrid must not access citation graph")

    monkeypatch.setattr(research_search_service, "ResearchSearchRunRepository", FakeRunRepository)
    monkeypatch.setattr(research_search_service, "KnowledgeBaseRepository", FakeKnowledgeBaseRepository)
    monkeypatch.setattr(research_search_service, "AcademicPaperRepository", FakeAcademicPaperRepository)
    monkeypatch.setattr(research_search_service, "knowledge_base", FakeKnowledgeBase())
    monkeypatch.setattr(research_search_service, "_rewrite_query", fake_rewrite)
    monkeypatch.setattr(
        research_search_service,
        "_public_model_config",
        lambda chat_model, reranker_model: {
            "chat_model": chat_model,
            "reranker_model": reranker_model,
            "chat_provider": "test",
            "reranker_provider": "test",
        },
    )
    monkeypatch.setattr(research_search_service.AcademicGraphService, "get_status", reject_graph_call)
    monkeypatch.setattr(research_search_service, "_apply_citation_graph", reject_graph_call)

    result = await research_search_service.search_papers(
        kb_id="kb-1",
        current_user=SimpleNamespace(uid="user-1", role="user", department_id=1),
        query="How does local retrieval work?",
        retrieval_mode=research_search_service.LOCAL_HYBRID_MODE,
        top_k=10,
        recall_top_k=50,
        year_from=None,
        year_to=None,
        chat_model=None,
        reranker_model="rerank:model",
    )

    assert created_run["retrieval_config"]["mode"] == research_search_service.LOCAL_HYBRID_MODE
    assert created_run["retrieval_config"]["citation_graph"] is False
    assert query_options["strict_research"] is True
    assert query_options["use_graph_retrieval"] is False
    assert result["config"]["mode"] == research_search_service.LOCAL_HYBRID_MODE
    assert result["graph_expansion"] is None
    assert result["total"] == 1
    assert result["items"][0]["paper_id"] == "paper-1"
    assert "graph_preflight_ms" not in result["stage_timings"]
    assert "citation_graph_ms" not in result["stage_timings"]
    assert run_updates[-1]["result_snapshot"] == result


@pytest.mark.asyncio
async def test_strict_graph_preflight_runs_before_query_rewrite(monkeypatch):
    run_updates = []

    class FakeRunRepository:
        async def create(self, **values):
            return None

        async def update(self, run_id, values):
            run_updates.append(values)

    class FakeKnowledgeBaseRepository:
        async def get_by_kb_id(self, kb_id):
            return SimpleNamespace(kb_type="milvus", llm_model_spec="chat:model")

    class FakeKnowledgeBase:
        async def check_accessible(self, user_info, kb_id):
            return True

    async def graph_not_ready(*args, **kwargs):
        return {"papers": 1, "citations": 0}

    async def reject_query_rewrite(*args, **kwargs):
        raise AssertionError("strict graph preflight must run before query rewrite")

    monkeypatch.setattr(research_search_service, "ResearchSearchRunRepository", FakeRunRepository)
    monkeypatch.setattr(research_search_service, "KnowledgeBaseRepository", FakeKnowledgeBaseRepository)
    monkeypatch.setattr(research_search_service, "knowledge_base", FakeKnowledgeBase())
    monkeypatch.setattr(research_search_service.AcademicGraphService, "get_status", graph_not_ready)
    monkeypatch.setattr(research_search_service, "_rewrite_query", reject_query_rewrite)
    monkeypatch.setattr(
        research_search_service,
        "_public_model_config",
        lambda chat_model, reranker_model: {
            "chat_model": chat_model,
            "reranker_model": reranker_model,
            "chat_provider": "test",
            "reranker_provider": "test",
        },
    )

    with pytest.raises(research_search_service.ResearchSearchError) as exc_info:
        await research_search_service.search_papers(
            kb_id="kb-1",
            current_user=SimpleNamespace(uid="user-1", role="user", department_id=1),
            query="How does strict graph retrieval work?",
            retrieval_mode=research_search_service.STRICT_HYBRID_GRAPH_MODE,
            top_k=10,
            recall_top_k=50,
            year_from=None,
            year_to=None,
            chat_model=None,
            reranker_model="rerank:model",
        )

    assert exc_info.value.error_type == "graph_not_ready"
    failed_run = run_updates[-1]
    assert failed_run["error_type"] == "graph_not_ready"
    assert failed_run["stage_timings"]["graph_preflight_ms"] >= 0
    assert "query_rewrite_ms" not in failed_run["stage_timings"]


@pytest.mark.asyncio
async def test_unexpected_search_failure_does_not_persist_provider_error_details(monkeypatch):
    run_updates = []

    class FakeRunRepository:
        async def create(self, **values):
            return None

        async def update(self, run_id, values):
            run_updates.append(values)

    class FakeKnowledgeBaseRepository:
        async def get_by_kb_id(self, kb_id):
            return SimpleNamespace(kb_type="milvus", llm_model_spec="chat:model")

    class FakeKnowledgeBase:
        async def check_accessible(self, user_info, kb_id):
            return True

    async def provider_failure(*args, **kwargs):
        raise RuntimeError("provider response Authorization=secret-api-key body=<private>")

    monkeypatch.setattr(research_search_service, "ResearchSearchRunRepository", FakeRunRepository)
    monkeypatch.setattr(research_search_service, "KnowledgeBaseRepository", FakeKnowledgeBaseRepository)
    monkeypatch.setattr(research_search_service, "knowledge_base", FakeKnowledgeBase())
    monkeypatch.setattr(research_search_service, "_rewrite_query", provider_failure)
    monkeypatch.setattr(
        research_search_service,
        "_public_model_config",
        lambda chat_model, reranker_model: {
            "chat_model": chat_model,
            "reranker_model": reranker_model,
            "chat_provider": "test",
            "reranker_provider": "test",
        },
    )

    with pytest.raises(research_search_service.ResearchSearchError) as exc_info:
        await research_search_service.search_papers(
            kb_id="kb-1",
            current_user=SimpleNamespace(uid="user-1", role="user", department_id=1),
            query="How does local retrieval work?",
            retrieval_mode=research_search_service.LOCAL_HYBRID_MODE,
            top_k=10,
            recall_top_k=50,
            year_from=None,
            year_to=None,
            chat_model=None,
            reranker_model="rerank:model",
        )

    assert exc_info.value.error_type == "local_research_failure"
    failed_run = run_updates[-1]
    assert failed_run["error_type"] == "local_research_failure"
    assert failed_run["error_message"] == "科研本地混合检索执行失败"
    assert "secret-api-key" not in str(run_updates)
    assert "<private>" not in str(run_updates)


@pytest.mark.asyncio
async def test_get_search_run_rechecks_knowledge_base_access(monkeypatch):
    checked_kb_ids = []

    async def deny_access(current_user, kb_id):
        checked_kb_ids.append(kb_id)
        raise HTTPException(status_code=403, detail="无权访问该知识库")

    class FakeRepository:
        async def get(self, run_id, *, uid):
            return SimpleNamespace(kb_id="kb-private")

    monkeypatch.setattr(research_search_service, "_ensure_access", deny_access)
    monkeypatch.setattr(research_search_service, "ResearchSearchRunRepository", FakeRepository)

    with pytest.raises(HTTPException) as exc_info:
        await research_search_service.get_search_run(
            run_id="run-1",
            current_user=SimpleNamespace(uid="user-1", role="user"),
        )

    assert exc_info.value.status_code == 403
    assert checked_kb_ids == ["kb-private"]


@pytest.mark.asyncio
async def test_list_search_runs_is_scoped_and_returns_pagination(monkeypatch):
    calls = {"access": [], "list": []}
    records = [SimpleNamespace(run_id="run-pinned"), SimpleNamespace(run_id="run-recent")]

    async def allow_access(current_user, kb_id):
        calls["access"].append((current_user.uid, kb_id))

    class FakeRepository:
        async def list_for_user(self, **values):
            calls["list"].append(values)
            return records, 3

        @staticmethod
        def serialize(record, *, include_result=False):
            return {"run_id": record.run_id, "include_result": include_result}

    monkeypatch.setattr(research_search_service, "_ensure_access", allow_access)
    monkeypatch.setattr(research_search_service, "ResearchSearchRunRepository", FakeRepository)

    result = await research_search_service.list_search_runs(
        kb_id="kb-research",
        current_user=SimpleNamespace(uid="user-1"),
        offset=0,
        limit=2,
    )

    assert calls == {
        "access": [("user-1", "kb-research")],
        "list": [{"kb_id": "kb-research", "uid": "user-1", "offset": 0, "limit": 2}],
    }
    assert result == {
        "items": [
            {"run_id": "run-pinned", "include_result": False},
            {"run_id": "run-recent", "include_result": False},
        ],
        "total": 3,
        "offset": 0,
        "limit": 2,
        "has_more": True,
    }


@pytest.mark.asyncio
async def test_set_search_run_pinned_updates_only_owned_run(monkeypatch):
    calls = {"access": [], "update": []}
    record = SimpleNamespace(run_id="run-1", kb_id="kb-research")
    updated = SimpleNamespace(run_id="run-1", kb_id="kb-research", is_pinned=True)

    async def allow_access(current_user, kb_id):
        calls["access"].append((current_user.uid, kb_id))

    class FakeRepository:
        async def get(self, run_id, *, uid):
            assert (run_id, uid) == ("run-1", "user-1")
            return record

        async def update(self, run_id, values):
            calls["update"].append((run_id, values))
            return updated

        @staticmethod
        def serialize(value, *, include_result=False):
            return {"run_id": value.run_id, "is_pinned": value.is_pinned}

    monkeypatch.setattr(research_search_service, "_ensure_access", allow_access)
    monkeypatch.setattr(research_search_service, "ResearchSearchRunRepository", FakeRepository)

    result = await research_search_service.set_search_run_pinned(
        run_id="run-1",
        current_user=SimpleNamespace(uid="user-1"),
        is_pinned=True,
    )

    assert result == {"run_id": "run-1", "is_pinned": True}
    assert calls == {
        "access": [("user-1", "kb-research")],
        "update": [("run-1", {"is_pinned": True})],
    }


@pytest.mark.asyncio
async def test_delete_search_run_rejects_running_record(monkeypatch):
    deleted = []

    async def allow_access(*args, **kwargs):
        return None

    class FakeRepository:
        async def get(self, run_id, *, uid):
            return SimpleNamespace(kb_id="kb-research", status="running")

        async def delete(self, run_id, *, uid):
            deleted.append((run_id, uid))
            return True

    monkeypatch.setattr(research_search_service, "_ensure_access", allow_access)
    monkeypatch.setattr(research_search_service, "ResearchSearchRunRepository", FakeRepository)

    with pytest.raises(research_search_service.ResearchSearchError) as exc_info:
        await research_search_service.delete_search_run(
            run_id="run-active",
            current_user=SimpleNamespace(uid="user-1"),
        )

    assert exc_info.value.error_type == "run_active"
    assert deleted == []


@pytest.mark.asyncio
async def test_delete_search_run_removes_completed_owned_record(monkeypatch):
    deleted = []

    async def allow_access(*args, **kwargs):
        return None

    class FakeRepository:
        async def get(self, run_id, *, uid):
            assert uid == "user-1"
            return SimpleNamespace(kb_id="kb-research", status="success")

        async def delete(self, run_id, *, uid):
            deleted.append((run_id, uid))
            return True

    monkeypatch.setattr(research_search_service, "_ensure_access", allow_access)
    monkeypatch.setattr(research_search_service, "ResearchSearchRunRepository", FakeRepository)

    await research_search_service.delete_search_run(
        run_id="run-completed",
        current_user=SimpleNamespace(uid="user-1"),
    )

    assert deleted == [("run-completed", "user-1")]
