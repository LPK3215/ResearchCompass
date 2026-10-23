"""跨库重复合并（元数据补全 + 源库归档）的分支测试。"""

from types import SimpleNamespace

import pytest

from yuxi.services import research_deliverable_service as deliverable

TITLE = "Attention Is All You Need"


def _paper(paper_id: str, *, kb_id: str = "kb-a", **overrides):
    values = {
        "paper_id": paper_id,
        "kb_id": kb_id,
        "title": TITLE,
        "doi": None,
        "abstract": None,
        "authors": [],
        "publication_year": None,
        "venue": None,
        "keywords": [],
        "citation_count": None,
        "file_id": None,
        "archived_at": None,
        "metadata_revision": 1,
        "metadata_status": "extracted",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class FakeRepository:
    def __init__(self, papers):
        self.papers = {(paper.kb_id, paper.paper_id): paper for paper in papers}
        self.updated = []
        self.archived = []

    async def get_by_paper_id(self, *, kb_id, paper_id):
        return self.papers.get((kb_id, paper_id))

    async def update_metadata(self, *, kb_id, paper_id, data):
        self.updated.append({"kb_id": kb_id, "paper_id": paper_id, "data": dict(data)})
        paper = self.papers[(kb_id, paper_id)]
        for key, value in data.items():
            setattr(paper, key, value)
        return paper

    async def archive_papers(self, *, kb_id, paper_ids):
        self.archived.append({"kb_id": kb_id, "paper_ids": list(paper_ids)})
        return [paper_id for paper_id in paper_ids if (kb_id, paper_id) in self.papers]


def _patch(monkeypatch, papers):
    repo = FakeRepository(papers)
    enqueued = []

    async def allow_access(*args, **kwargs):
        return None

    async def enqueue_reindex(**kwargs):
        enqueued.append(kwargs)
        return SimpleNamespace(id="task-reindex", status="pending"), True

    monkeypatch.setattr(deliverable, "_ensure_access", allow_access)
    monkeypatch.setattr(deliverable, "AcademicPaperRepository", lambda: repo)
    monkeypatch.setattr(deliverable, "_enqueue_paper_reindex", enqueue_reindex)
    return repo, enqueued


@pytest.mark.asyncio
async def test_merge_fills_blank_fields_and_archives_source(monkeypatch):
    target = _paper(
        "p1", file_id="file-a", venue="ICLR", metadata_status="verified", abstract="  "
    )
    source = _paper(
        "p2",
        kb_id="kb-b",
        doi="10.1000/abc",
        abstract="Source abstract",
        authors=["Ashish Vaswani"],
        publication_year=2017,
        venue="NeurIPS",
        keywords=["NLP"],
        citation_count=42,
        file_id="file-b",
    )
    repo, enqueued = _patch(monkeypatch, [target, source])

    result = await deliverable.merge_cross_library_duplicate(
        kb_id="kb-a",
        paper_id="p1",
        source_kb_id="kb-b",
        source_paper_id="p2",
        current_user=SimpleNamespace(uid="user-1"),
    )

    assert result["filled_fields"] == [
        "abstract",
        "authors",
        "citation_count",
        "doi",
        "keywords",
        "publication_year",
    ]
    assert result["archived_source_paper"] is True
    assert result["reindex_task"] == {"task_id": "task-reindex", "status": "pending"}

    # 只写目标库记录，非空字段（title / venue）不覆盖
    assert repo.updated == [
        {
            "kb_id": "kb-a",
            "paper_id": "p1",
            "data": {
                "doi": "10.1000/abc",
                "abstract": "Source abstract",
                "authors": ["Ashish Vaswani"],
                "publication_year": 2017,
                "keywords": ["NLP"],
                "citation_count": 42,
                "metadata_status": "pending_reindex",
                "metadata_error": None,
            },
        }
    ]
    assert target.venue == "ICLR"
    assert target.file_id == "file-a"
    # 源库只归档、不改元数据
    assert repo.archived == [{"kb_id": "kb-b", "paper_ids": ["p2"]}]
    assert enqueued[0]["kb_id"] == "kb-a"
    assert enqueued[0]["revision"] == 1


@pytest.mark.asyncio
async def test_merge_skips_reindex_for_metadata_only_target(monkeypatch):
    target = _paper("p1")
    source = _paper("p2", kb_id="kb-b", doi="10.1000/abc", abstract="Source abstract")
    repo, enqueued = _patch(monkeypatch, [target, source])

    result = await deliverable.merge_cross_library_duplicate(
        kb_id="kb-a",
        paper_id="p1",
        source_kb_id="kb-b",
        source_paper_id="p2",
        current_user=SimpleNamespace(uid="user-1"),
    )

    assert result["reindex_task"] is None
    assert repo.updated[0]["data"]["metadata_status"] == "extracted"
    assert enqueued == []


@pytest.mark.asyncio
async def test_merge_rejects_same_library(monkeypatch):
    repo, _ = _patch(monkeypatch, [_paper("p1")])

    with pytest.raises(deliverable.ResearchDeliverableError) as exc_info:
        await deliverable.merge_cross_library_duplicate(
            kb_id="kb-a",
            paper_id="p1",
            source_kb_id="kb-a",
            source_paper_id="p2",
            current_user=SimpleNamespace(uid="user-1"),
        )

    assert exc_info.value.error_type == "cross_library_same"
    assert repo.archived == []


@pytest.mark.asyncio
async def test_merge_rejects_different_paper(monkeypatch):
    target = _paper("p1")
    source = _paper("p2", kb_id="kb-b", title="A Completely Different Paper", doi="10.9/other")
    repo, _ = _patch(monkeypatch, [target, source])

    with pytest.raises(deliverable.ResearchDeliverableError) as exc_info:
        await deliverable.merge_cross_library_duplicate(
            kb_id="kb-a",
            paper_id="p1",
            source_kb_id="kb-b",
            source_paper_id="p2",
            current_user=SimpleNamespace(uid="user-1"),
        )

    assert exc_info.value.error_type == "cross_library_not_same_paper"
    assert repo.archived == []


@pytest.mark.asyncio
async def test_merge_rejects_losing_source_fulltext(monkeypatch):
    target = _paper("p1", file_id=None)
    source = _paper("p2", kb_id="kb-b", file_id="file-b")
    repo, _ = _patch(monkeypatch, [target, source])

    with pytest.raises(deliverable.ResearchDeliverableError) as exc_info:
        await deliverable.merge_cross_library_duplicate(
            kb_id="kb-a",
            paper_id="p1",
            source_kb_id="kb-b",
            source_paper_id="p2",
            current_user=SimpleNamespace(uid="user-1"),
        )

    assert exc_info.value.error_type == "cross_library_would_lose_fulltext"
    assert repo.archived == []


@pytest.mark.asyncio
async def test_merge_rejects_archived_paper(monkeypatch):
    target = _paper("p1")
    source = _paper("p2", kb_id="kb-b", archived_at="2026-10-20T10:00:00")
    repo, _ = _patch(monkeypatch, [target, source])

    with pytest.raises(deliverable.ResearchDeliverableError) as exc_info:
        await deliverable.merge_cross_library_duplicate(
            kb_id="kb-a",
            paper_id="p1",
            source_kb_id="kb-b",
            source_paper_id="p2",
            current_user=SimpleNamespace(user_id="user-1"),
        )

    assert exc_info.value.error_type == "paper_archived"
    assert repo.archived == []
