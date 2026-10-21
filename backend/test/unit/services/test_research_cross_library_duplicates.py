"""跨库查重的可访问范围与跨库分组行为测试。"""

from types import SimpleNamespace

import pytest

from yuxi.services import research_deliverable_service as deliverable


def _paper(paper_id: str, title: str, *, doi: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(paper_id=paper_id, title=title, doi=doi, publication_year=2024)


class FakePaperRepository:
    def __init__(self, papers_by_kb):
        self.papers_by_kb = papers_by_kb

    async def iter_export_batches(self, *, kb_id, batch_size=200):
        yield self.papers_by_kb.get(kb_id, [])


@pytest.fixture
def fake_libraries(monkeypatch):
    libraries = [
        SimpleNamespace(kb_id="kb-a", name="Library A", kb_type="milvus"),
        SimpleNamespace(kb_id="kb-b", name="Library B", kb_type="milvus"),
        SimpleNamespace(kb_id="kb-c", name="Restricted", kb_type="milvus"),
        SimpleNamespace(kb_id="kb-x", name="External", kb_type="dify"),
    ]

    class FakeKBRepository:
        async def get_all(self):
            return libraries

    async def check_accessible(user_info, kb_id):
        return kb_id != "kb-c"

    from yuxi.knowledge.runtime import knowledge_base as kb_runtime

    monkeypatch.setattr("yuxi.repositories.knowledge_base_repository.KnowledgeBaseRepository", FakeKBRepository)
    monkeypatch.setattr(kb_runtime, "check_accessible", check_accessible)


@pytest.mark.asyncio
async def test_cross_library_reports_only_papers_present_in_multiple_libraries(monkeypatch, fake_libraries):
    monkeypatch.setattr(
        deliverable,
        "AcademicPaperRepository",
        lambda: FakePaperRepository(
            {
                "kb-a": [_paper("a1", "Shared", doi="10.1/shared"), _paper("a2", "Local", doi="10.1/local")],
                "kb-b": [_paper("b1", "Shared", doi="10.1/shared")],
            }
        ),
    )

    result = await deliverable.find_cross_library_duplicates(
        current_user=SimpleNamespace(uid="user-1", role="user", department_id=1)
    )

    assert result["library_count"] == 2
    assert [item["kb_id"] for item in result["libraries"]] == ["kb-a", "kb-b"]
    assert result["duplicate_group_count"] == 1
    assert result["duplicate_paper_count"] == 2
    group = result["groups"][0]
    assert group["key"] == "doi:10.1/shared"
    assert group["library_count"] == 2
    assert {item["kb_id"] for item in group["papers"]} == {"kb-a", "kb-b"}


@pytest.mark.asyncio
async def test_cross_library_ignores_duplicates_inside_one_library(monkeypatch, fake_libraries):
    monkeypatch.setattr(
        deliverable,
        "AcademicPaperRepository",
        lambda: FakePaperRepository(
            {
                "kb-a": [_paper("a1", "Twice", doi="10.1/twice"), _paper("a2", "Twice", doi="10.1/twice")],
                "kb-b": [],
            }
        ),
    )

    result = await deliverable.find_cross_library_duplicates(
        current_user=SimpleNamespace(uid="user-1", role="user", department_id=1)
    )

    assert result["duplicate_group_count"] == 0
    assert result["duplicate_paper_count"] == 0


@pytest.mark.asyncio
async def test_cross_library_requires_two_accessible_libraries(monkeypatch, fake_libraries):
    monkeypatch.setattr(deliverable, "AcademicPaperRepository", lambda: FakePaperRepository({}))

    async def deny_all(user_info, kb_id):
        return False

    from yuxi.knowledge.runtime import knowledge_base as kb_runtime

    monkeypatch.setattr(kb_runtime, "check_accessible", deny_all)

    with pytest.raises(deliverable.ResearchDeliverableError) as exc_info:
        await deliverable.find_cross_library_duplicates(
            current_user=SimpleNamespace(uid="user-1", role="user", department_id=1)
        )

    assert exc_info.value.error_type == "cross_library_insufficient"
