"""研究趋势全量聚合服务测试。"""

from types import SimpleNamespace

import pytest

from yuxi.services import academic_trend_service


@pytest.mark.asyncio
async def test_get_academic_trends_aggregates_every_batch(monkeypatch):
    async def allow_access(*args, **kwargs):
        return None

    class FakePaperRepository:
        async def iter_trend_record_batches(self, **kwargs):
            yield [(2023, ["GNN"], 3), (2024, ["LLM"], 5)]
            yield [(2024, ["gnn", "LLM"], 7)]

    class FakeGraphRepository:
        async def citation_trend(self, **kwargs):
            return []

    monkeypatch.setattr(academic_trend_service, "_ensure_access", allow_access)
    monkeypatch.setattr(academic_trend_service, "AcademicPaperRepository", FakePaperRepository)
    monkeypatch.setattr(academic_trend_service, "AcademicGraphRepository", FakeGraphRepository)

    result = await academic_trend_service.get_academic_trends(
        kb_id="kb-1",
        current_user=SimpleNamespace(uid="user-1"),
        year_from=None,
        year_to=None,
        offset=0,
        limit=100,
        top_keywords=20,
    )

    assert result["scope"] == {
        "year_from": None,
        "year_to": None,
        "total_papers": 3,
        "returned_papers": 3,
        "has_more": False,
    }
    assert result["publication_trend"] == [
        {"year": 2023, "papers": 1, "citations": 3},
        {"year": 2024, "papers": 2, "citations": 12},
    ]
    assert next(item for item in result["keyword_trends"] if item["keyword"].casefold() == "gnn")["total"] == 2


@pytest.mark.asyncio
async def test_get_academic_trends_rejects_partial_pagination(monkeypatch):
    async def allow_access(*args, **kwargs):
        return None

    monkeypatch.setattr(academic_trend_service, "_ensure_access", allow_access)

    with pytest.raises(academic_trend_service.AcademicTrendError) as exc_info:
        await academic_trend_service.get_academic_trends(
            kb_id="kb-1",
            current_user=SimpleNamespace(uid="user-1"),
            year_from=None,
            year_to=None,
            offset=1,
            limit=100,
            top_keywords=20,
        )

    assert exc_info.value.error_type == "trend_pagination_unsupported"
