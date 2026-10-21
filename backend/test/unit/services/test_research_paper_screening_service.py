"""research_paper_screening_service 的边界行为测试。"""

from types import SimpleNamespace

import pytest

from yuxi.services import research_paper_screening_service


def _paper(paper_id: str, title: str, *, year: int = 2024, doi: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        paper_id=paper_id,
        title=title,
        publication_year=year,
        venue="Test Journal",
        doi=doi,
    )


class FakeRepository:
    def __init__(self, papers, screenings=None):
        self.papers = papers
        self.screenings = screenings or {}
        self.set_calls = []
        self.clear_calls = []

    async def list_screenings(self, *, kb_id, uid):
        return self.screenings

    async def count_screening_summary(self, *, kb_id, uid):
        counts = {"identified": len(self.papers), "included": 0, "excluded": 0, "pending": 0}
        for screening in self.screenings.values():
            counts[screening["decision"]] += 1
        counts["pending"] = counts["identified"] - counts["included"] - counts["excluded"]
        return counts

    async def iter_export_batches(self, *, kb_id, batch_size=200):
        yield self.papers

    async def list_by_paper_ids(self, *, kb_id, paper_ids):
        known = {paper.paper_id for paper in self.papers}
        return [paper for paper in self.papers if paper.paper_id in set(paper_ids) & known]

    async def set_screening(self, **kwargs):
        self.set_calls.append(kwargs)

    async def clear_screenings(self, *, kb_id, paper_ids, uid):
        self.clear_calls.append({"kb_id": kb_id, "paper_ids": paper_ids, "uid": uid})
        return len(paper_ids)


@pytest.fixture
def allow_access(monkeypatch):
    async def _allow(*args, **kwargs):
        return None

    monkeypatch.setattr(research_paper_screening_service, "_ensure_access", _allow)


@pytest.mark.asyncio
async def test_get_screening_counts_decisions_and_filters_by_paper_ids(monkeypatch, allow_access):
    papers = [_paper("p1", "A"), _paper("p2", "B"), _paper("p3", "C")]
    repository = FakeRepository(papers, {"p1": {"decision": "included", "reason": ""}})
    monkeypatch.setattr(research_paper_screening_service, "AcademicPaperRepository", lambda: repository)

    result = await research_paper_screening_service.get_paper_screening(
        kb_id="kb-1",
        current_user=SimpleNamespace(uid="user-1"),
        paper_ids=["p1", "p2"],
    )

    assert result["counts"] == {"identified": 3, "included": 1, "excluded": 0, "pending": 2}
    assert [item["paper_id"] for item in result["items"]] == ["p1", "p2"]
    assert result["items"][0]["decision"] == "included"
    assert result["items"][1]["decision"] == "pending"


@pytest.mark.asyncio
async def test_set_screening_writes_each_paper_with_reason(monkeypatch, allow_access):
    repository = FakeRepository([_paper("p1", "A"), _paper("p2", "B")])
    monkeypatch.setattr(research_paper_screening_service, "AcademicPaperRepository", lambda: repository)

    result = await research_paper_screening_service.set_paper_screening(
        kb_id="kb-1",
        paper_ids=["p1", "p2"],
        decision="excluded",
        reason="  非同行评审  ",
        current_user=SimpleNamespace(uid="user-1"),
    )

    assert result == {"decision": "excluded", "updated": 2, "paper_ids": ["p1", "p2"]}
    assert [call["paper_id"] for call in repository.set_calls] == ["p1", "p2"]
    assert {call["reason"] for call in repository.set_calls} == {"非同行评审"}
    assert repository.clear_calls == []


@pytest.mark.asyncio
async def test_set_screening_pending_clears_decisions(monkeypatch, allow_access):
    repository = FakeRepository([_paper("p1", "A")])
    monkeypatch.setattr(research_paper_screening_service, "AcademicPaperRepository", lambda: repository)

    result = await research_paper_screening_service.set_paper_screening(
        kb_id="kb-1",
        paper_ids=["p1"],
        decision="pending",
        reason="",
        current_user=SimpleNamespace(uid="user-1"),
    )

    assert result == {"decision": "pending", "updated": 1, "paper_ids": ["p1"]}
    assert repository.clear_calls == [{"kb_id": "kb-1", "paper_ids": ["p1"], "uid": "user-1"}]
    assert repository.set_calls == []


@pytest.mark.asyncio
async def test_set_screening_rejects_unknown_paper(monkeypatch, allow_access):
    repository = FakeRepository([_paper("p1", "A")])
    monkeypatch.setattr(research_paper_screening_service, "AcademicPaperRepository", lambda: repository)

    with pytest.raises(research_paper_screening_service.ResearchScreeningError) as exc_info:
        await research_paper_screening_service.set_paper_screening(
            kb_id="kb-1",
            paper_ids=["p1", "missing"],
            decision="included",
            reason="",
            current_user=SimpleNamespace(uid="user-1"),
        )

    assert exc_info.value.error_type == "paper_not_found"
    assert repository.set_calls == []


@pytest.mark.asyncio
async def test_export_screening_csv_contains_counts_and_neutralized_cells(monkeypatch, allow_access):
    papers = [_paper("p1", "=SUM(A1)"), _paper("p2", "B")]
    repository = FakeRepository(
        papers,
        {
            "p1": {"decision": "excluded", "reason": "@不相关"},
            "p2": {"decision": "included", "reason": ""},
        },
    )
    monkeypatch.setattr(research_paper_screening_service, "AcademicPaperRepository", lambda: repository)

    filename, content, media_type = await research_paper_screening_service.export_screening_csv(
        kb_id="kb-1", current_user=SimpleNamespace(uid="user-1")
    )

    text = content.decode("utf-8-sig")
    assert filename == "paper_screening_kb-1.csv"
    assert media_type == "text/csv; charset=utf-8"
    assert "识别 2 / 纳入 1 / 排除 1 / 待定 0" in text
    assert "'=SUM(A1)" in text
    assert "'@不相关" in text
    assert "排除" in text and "纳入" in text


@pytest.mark.asyncio
async def test_set_screening_rejects_archived_papers(monkeypatch, allow_access):
    archived = _paper("p1", "Archived")
    archived.archived_at = object()
    repository = FakeRepository([archived])
    monkeypatch.setattr(research_paper_screening_service, "AcademicPaperRepository", lambda: repository)

    with pytest.raises(research_paper_screening_service.ResearchScreeningError) as exc_info:
        await research_paper_screening_service.set_paper_screening(
            kb_id="kb-1",
            paper_ids=["p1"],
            decision="included",
            reason="",
            current_user=SimpleNamespace(uid="user-1"),
        )

    assert exc_info.value.error_type == "paper_not_found"
    assert repository.set_calls == []
