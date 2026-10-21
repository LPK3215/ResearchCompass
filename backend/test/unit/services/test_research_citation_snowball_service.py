"""引文滚雪球的幂等与全文入队行为测试。"""

from types import SimpleNamespace

import pytest

from yuxi.services import academic_paper_import_service, research_citation_snowball_service as snowball


def _edge(paper_id: str, title: str, *, doi: str | None = None, oa_url: str | None = None) -> dict:
    return {
        "contexts": [],
        "intents": [],
        "isInfluential": False,
        "citedPaper": {
            "paperId": paper_id,
            "title": title,
            "externalIds": {"DOI": doi} if doi else {},
            "year": 2023,
            "venue": "NeurIPS",
            "authors": [{"name": "Ada Lovelace"}],
            "citationCount": 12,
            "isOpenAccess": bool(oa_url),
            "openAccessPdf": {"url": oa_url} if oa_url else None,
        },
    }


class FakeRepository:
    def __init__(self, existing=None):
        self.existing = existing or []
        self.created_rows = []
        self.include_archived = None

    async def get_by_paper_id(self, *, kb_id, paper_id):
        return SimpleNamespace(paper_id=paper_id, title="Seed Paper", doi="10.1/seed", external_ids={})

    async def iter_export_batches(self, *, kb_id, batch_size=200, include_archived=False):
        self.include_archived = include_archived
        yield self.existing

    async def create_metadata_only_batch(self, *, kb_id, rows):
        self.created_rows = rows
        return [SimpleNamespace(paper_id=row["paper_id"]) for row in rows], 0


class FakeClient:
    def __init__(self, edges):
        self.edges = edges

    async def resolve_paper(self, paper):
        return {"paperId": "s2-seed"}

    async def list_references(self, paper_id, limit):
        return self.edges

    async def list_citations(self, paper_id, limit):
        return self.edges


@pytest.fixture
def allow_access(monkeypatch):
    async def _allow(*args, **kwargs):
        return None

    monkeypatch.setattr(snowball, "_ensure_access", _allow)


@pytest.mark.asyncio
async def test_list_related_marks_papers_already_in_library(monkeypatch, allow_access):
    repository = FakeRepository(existing=[SimpleNamespace(paper_id="p1", title="Known Paper", doi="10.1/known")])
    monkeypatch.setattr(snowball, "AcademicPaperRepository", lambda: repository)
    monkeypatch.setattr(
        snowball,
        "SemanticScholarClient",
        lambda: FakeClient(
            [_edge("s2-1", "Known Paper", doi="10.1/known"), _edge("s2-2", "New Paper", doi="10.1/new")]
        ),
    )

    result = await snowball.list_related_papers(
        kb_id="kb-1",
        paper_id="paper-1",
        direction="references",
        current_user=SimpleNamespace(uid="user-1"),
        limit=10,
    )

    assert result["total"] == 2
    assert result["existing_count"] == 1
    assert repository.include_archived is True
    assert [item["already_in_library"] for item in result["items"]] == [True, False]
    assert result["items"][0]["s2_paper_id"] == "s2-1"


@pytest.mark.asyncio
async def test_import_related_writes_metadata_and_skips_existing(monkeypatch, allow_access):
    repository = FakeRepository(existing=[SimpleNamespace(paper_id="p1", title="Known Paper", doi="10.1/known")])
    monkeypatch.setattr(snowball, "AcademicPaperRepository", lambda: repository)
    monkeypatch.setattr(
        snowball,
        "SemanticScholarClient",
        lambda: FakeClient(
            [
                _edge("s2-1", "Known Paper", doi="10.1/known"),
                _edge("s2-2", "New Paper", doi="10.1/new", oa_url="https://example.org/new.pdf"),
            ]
        ),
    )

    result = await snowball.import_related_papers(
        kb_id="kb-1",
        paper_id="paper-1",
        direction="references",
        paper_ids=["s2-1", "s2-2"],
        include_full_text=False,
        current_user=SimpleNamespace(uid="user-1"),
    )

    assert result["metadata_imported"] == 1
    assert result["skipped"] == 1
    assert result["full_text_queued"] == 0
    assert repository.created_rows[0]["title"] == "New Paper"
    assert repository.created_rows[0]["metadata_source"] == "citation_snowball"
    assert repository.created_rows[0]["file_id"] is None


@pytest.mark.asyncio
async def test_import_related_queues_open_access_full_text_without_metadata_row(monkeypatch, allow_access):
    repository = FakeRepository()
    monkeypatch.setattr(snowball, "AcademicPaperRepository", lambda: repository)
    monkeypatch.setattr(
        snowball,
        "SemanticScholarClient",
        lambda: FakeClient(
            [
                _edge("s2-1", "Open Paper", doi="10.1/open", oa_url="https://example.org/open.pdf"),
                _edge("s2-2", "Closed Paper", doi="10.1/closed"),
            ]
        ),
    )
    queued = []

    async def fake_import_external_paper(*, identifier, kb_id, current_user):
        queued.append(identifier)
        return {"task_id": "task-1"}

    monkeypatch.setattr(academic_paper_import_service, "import_external_paper", fake_import_external_paper)

    result = await snowball.import_related_papers(
        kb_id="kb-1",
        paper_id="paper-1",
        direction="references",
        paper_ids=["s2-1", "s2-2"],
        include_full_text=True,
        current_user=SimpleNamespace(uid="user-1"),
    )

    assert queued == ["s2-1"]
    assert result["full_text_queued"] == 1
    assert result["metadata_imported"] == 1
    assert [row["title"] for row in repository.created_rows] == ["Closed Paper"]
