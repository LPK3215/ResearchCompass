from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from server.routers import knowledge_eval_router


pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


@pytest.mark.parametrize(
    ("endpoint", "write"),
    [
        (knowledge_eval_router.download_evaluation_dataset, False),
        (knowledge_eval_router.delete_evaluation_dataset, True),
    ],
)
async def test_dataset_id_routes_authorize_owning_kb_before_access(monkeypatch, endpoint, write):
    calls = {"access": [], "export": 0, "delete": 0}

    class FakeEvaluationRepository:
        async def get_dataset(self, dataset_id: str):
            assert dataset_id == "dataset-other"
            return SimpleNamespace(kb_id="kb-owner")

    class FakeEvaluationService:
        eval_repo = FakeEvaluationRepository()

        async def export_dataset_jsonl(self, _dataset_id: str):
            calls["export"] += 1
            return {"filename": "dataset.jsonl", "content": ""}

        async def delete_dataset(self, _dataset_id: str):
            calls["delete"] += 1

    async def deny_access(current_user, kb_id: str, *, write: bool = False):
        calls["access"].append((current_user.uid, kb_id, write))
        raise HTTPException(status_code=403, detail="forbidden")

    monkeypatch.setattr(knowledge_eval_router, "EvaluationService", FakeEvaluationService)
    monkeypatch.setattr(knowledge_eval_router, "_ensure_access", deny_access)

    with pytest.raises(HTTPException) as exc_info:
        await endpoint("dataset-other", current_user=SimpleNamespace(uid="admin-a"))

    assert exc_info.value.status_code == 403
    assert calls == {
        "access": [("admin-a", "kb-owner", write)],
        "export": 0,
        "delete": 0,
    }


async def test_evaluation_router_does_not_expose_internal_exception(monkeypatch):
    secret = "Authorization=secret-api-key provider-body=<private>"

    class FakeEvaluationService:
        async def list_datasets(self, kb_id: str):
            raise RuntimeError(secret)

    async def allow_access(*args, **kwargs):
        return None

    monkeypatch.setattr(knowledge_eval_router, "EvaluationService", FakeEvaluationService)
    monkeypatch.setattr(knowledge_eval_router, "_ensure_access", allow_access)

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_eval_router.list_evaluation_datasets(
            "kb-1",
            current_user=SimpleNamespace(uid="admin-a"),
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "获取评估数据集列表失败"
    assert secret not in str(exc_info.value.detail)


async def test_corpus_manifest_route_authorizes_kb_before_export(monkeypatch):
    calls = {"access": [], "export": 0}

    class FakeEvaluationService:
        async def export_corpus_manifest(self, _kb_id: str):
            calls["export"] += 1
            return {"filename": "manifest.json", "content": "{}\n"}

    async def deny_access(current_user, kb_id: str):
        calls["access"].append((current_user.uid, kb_id))
        raise HTTPException(status_code=403, detail="forbidden")

    monkeypatch.setattr(knowledge_eval_router, "EvaluationService", FakeEvaluationService)
    monkeypatch.setattr(knowledge_eval_router, "_ensure_access", deny_access)

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_eval_router.download_corpus_manifest(
            "kb-private",
            current_user=SimpleNamespace(uid="admin-a"),
        )

    assert exc_info.value.status_code == 403
    assert calls == {"access": [("admin-a", "kb-private")], "export": 0}


async def test_corpus_manifest_route_returns_download_response(monkeypatch):
    async def allow_access(*args, **kwargs):
        return None

    class FakeEvaluationService:
        async def export_corpus_manifest(self, kb_id: str):
            assert kb_id == "kb-research"
            return {"filename": "researchcompass-corpus-a1b2c3.json", "content": '{"document_count":2}\n'}

    monkeypatch.setattr(knowledge_eval_router, "EvaluationService", FakeEvaluationService)
    monkeypatch.setattr(knowledge_eval_router, "_ensure_access", allow_access)

    response = await knowledge_eval_router.download_corpus_manifest(
        "kb-research",
        current_user=SimpleNamespace(uid="admin-a"),
    )

    assert response.status_code == 200
    assert response.media_type == "application/json"
    assert response.body == b'{"document_count":2}\n'
    assert response.headers["content-disposition"] == (
        "attachment; filename*=UTF-8''researchcompass-corpus-a1b2c3.json"
    )
