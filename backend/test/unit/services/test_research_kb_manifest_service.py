"""知识库迁移清单校验的匹配与差异行为测试。"""

from types import SimpleNamespace

import pytest

from yuxi.services import research_deliverable_service as deliverable


def _paper(paper_id: str, title: str, *, doi: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(paper_id=paper_id, title=title, doi=doi)


class FakeRepository:
    def __init__(self, papers):
        self.papers = papers

    async def iter_export_batches(self, *, kb_id, batch_size=200):
        yield self.papers


@pytest.fixture
def allow_access(monkeypatch):
    async def _allow(*args, **kwargs):
        return None

    monkeypatch.setattr(deliverable, "_ensure_access", _allow)


@pytest.mark.asyncio
async def test_manifest_matches_by_paper_id_then_doi(monkeypatch, allow_access):
    repository = FakeRepository([_paper("p1", "Known", doi="10.1/a"), _paper("p2", "Moved", doi="10.1/b")])
    monkeypatch.setattr(deliverable, "AcademicPaperRepository", lambda: repository)

    result = await deliverable.validate_kb_manifest(
        kb_id="kb-1",
        manifest={
            "papers": [
                {"paper_id": "p1", "title": "Known", "doi": "10.1/a"},
                {"paper_id": "old-id", "title": "Moved", "doi": "https://doi.org/10.1/B"},
            ]
        },
        current_user=SimpleNamespace(uid="user-1"),
    )

    assert result["consistent"] is True
    assert result["matched_count"] == 2
    assert result["missing_count"] == 0
    assert result["extra_count"] == 0


@pytest.mark.asyncio
async def test_manifest_reports_missing_and_extra_entries(monkeypatch, allow_access):
    repository = FakeRepository([_paper("p1", "Known", doi="10.1/a"), _paper("p3", "Newcomer")])
    monkeypatch.setattr(deliverable, "AcademicPaperRepository", lambda: repository)

    result = await deliverable.validate_kb_manifest(
        kb_id="kb-1",
        manifest={
            "papers": [{"paper_id": "p1", "title": "Known", "doi": "10.1/a"}, {"paper_id": "p2", "title": "Lost"}]
        },
        current_user=SimpleNamespace(uid="user-1"),
    )

    assert result["consistent"] is False
    assert result["manifest_paper_count"] == 2
    assert result["library_paper_count"] == 2
    assert result["matched_count"] == 1
    assert [item["paper_id"] for item in result["missing"]] == ["p2"]
    assert [item["paper_id"] for item in result["extra"]] == ["p3"]


@pytest.mark.asyncio
async def test_manifest_rejects_invalid_payload(monkeypatch, allow_access):
    monkeypatch.setattr(deliverable, "AcademicPaperRepository", lambda: FakeRepository([]))

    with pytest.raises(deliverable.ResearchDeliverableError) as exc_info:
        await deliverable.validate_kb_manifest(
            kb_id="kb-1", manifest={"papers": "not-a-list"}, current_user=SimpleNamespace(uid="user-1")
        )

    assert exc_info.value.error_type == "manifest_invalid"

    with pytest.raises(deliverable.ResearchDeliverableError) as empty_exc:
        await deliverable.validate_kb_manifest(
            kb_id="kb-1", manifest={"papers": []}, current_user=SimpleNamespace(uid="user-1")
        )

    assert empty_exc.value.error_type == "manifest_empty"
