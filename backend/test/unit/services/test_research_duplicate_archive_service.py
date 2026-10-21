"""重复合并归档语义测试（G-37）。

验证合并只写归档状态、不改 metadata_status；恢复按归档状态还原；归档清单可读。
仓储层过滤条件依赖真实 PostgreSQL，由集成验收覆盖，这里只测服务行为。
"""

from datetime import datetime
from types import SimpleNamespace

import pytest

from yuxi.services import research_deliverable_service as service


class FakePaperRepository:
    def __init__(self, archived=("paper-2",), restored=("paper-2",), archived_papers=None):
        self.archive_result = list(archived)
        self.restore_result = list(restored)
        self.archived_papers = archived_papers or []
        self.archive_calls = []
        self.restore_calls = []
        self.list_calls = []

    async def archive_papers(self, **kwargs):
        self.archive_calls.append(kwargs)
        return self.archive_result

    async def restore_papers(self, **kwargs):
        self.restore_calls.append(kwargs)
        return self.restore_result

    async def list_archived_papers(self, **kwargs):
        self.list_calls.append(kwargs)
        return self.archived_papers

    async def update_metadata(self, **kwargs):
        raise AssertionError("归档不应再通过 metadata_status 标记")


def _patch(monkeypatch, repo, duplicates=None):
    access_calls = []

    async def allow_access(*args, **kwargs):
        access_calls.append(kwargs)
        return None

    monkeypatch.setattr(service, "_ensure_access", allow_access)
    monkeypatch.setattr(service, "AcademicPaperRepository", lambda: repo)
    if duplicates is not None:
        monkeypatch.setattr(service, "find_duplicate_papers", duplicates)
    return access_calls


@pytest.mark.asyncio
async def test_merge_archives_non_keep_papers_without_touching_metadata_status(monkeypatch):
    repo = FakePaperRepository()

    async def duplicates(*, kb_id, current_user):
        return {
            "groups": [
                {
                    "key": "doi:10.1/x",
                    "recommended_paper_id": "paper-1",
                    "papers": [{"paper_id": "paper-1"}, {"paper_id": "paper-2"}],
                }
            ]
        }

    access_calls = _patch(monkeypatch, repo, duplicates)

    result = await service.merge_duplicate_group(
        kb_id="kb-1", key="doi:10.1/x", current_user=SimpleNamespace(uid="user-1")
    )

    assert access_calls == [{"write": True}]
    assert repo.archive_calls == [{"kb_id": "kb-1", "paper_ids": ["paper-2"]}]
    assert result["kept_paper_id"] == "paper-1"
    assert result["archived_paper_ids"] == ["paper-2"]
    assert "metadata_status" in result["message"]


@pytest.mark.asyncio
async def test_merge_rejects_unknown_group(monkeypatch):
    repo = FakePaperRepository()

    async def duplicates(*, kb_id, current_user):
        return {"groups": []}

    _patch(monkeypatch, repo, duplicates)

    with pytest.raises(service.ResearchDeliverableError) as exc_info:
        await service.merge_duplicate_group(
            kb_id="kb-1", key="doi:10.1/missing", current_user=SimpleNamespace(uid="user-1")
        )

    assert exc_info.value.error_type == "duplicate_group_not_found"
    assert repo.archive_calls == []


@pytest.mark.asyncio
async def test_restore_clears_archive_state(monkeypatch):
    repo = FakePaperRepository(restored=("paper-2", "paper-3"))
    access_calls = _patch(monkeypatch, repo)

    result = await service.restore_archived_papers(
        kb_id="kb-1", paper_ids=["paper-2", "paper-3"], current_user=SimpleNamespace(uid="user-1")
    )

    assert access_calls == [{"write": True}]
    assert repo.restore_calls == [{"kb_id": "kb-1", "paper_ids": ["paper-2", "paper-3"]}]
    assert result == {"restored_paper_ids": ["paper-2", "paper-3"], "count": 2}


@pytest.mark.asyncio
async def test_list_archived_papers_reports_archive_time_and_metadata_status(monkeypatch):
    archived_at = datetime(2026, 10, 21, 9, 30)
    repo = FakePaperRepository(
        archived_papers=[
            SimpleNamespace(
                paper_id="paper-2",
                title="Duplicate Entry",
                publication_year=2020,
                doi=None,
                metadata_status="verified",
                archived_at=archived_at,
            )
        ]
    )
    access_calls = _patch(monkeypatch, repo)

    result = await service.list_archived_papers(kb_id="kb-1", current_user=SimpleNamespace(uid="user-1"), limit=20)

    assert access_calls == [{}]
    assert repo.list_calls == [{"kb_id": "kb-1", "limit": 20}]
    assert result["count"] == 1
    assert result["items"] == [
        {
            "paper_id": "paper-2",
            "title": "Duplicate Entry",
            "publication_year": 2020,
            "doi": "",
            "metadata_status": "verified",
            "archived_at": archived_at.isoformat(),
        }
    ]


@pytest.mark.asyncio
async def test_find_duplicates_prefers_fulltext_then_earliest_year(monkeypatch):
    class DuplicateRepository:
        async def iter_export_batches(self, *, kb_id, batch_size=200):
            yield [
                SimpleNamespace(
                    paper_id="meta", title="Same Paper", doi=None, publication_year=None, file_id=None
                ),
                SimpleNamespace(
                    paper_id="full-2020", title="Same Paper", doi=None, publication_year=2020, file_id="file-2020"
                ),
                SimpleNamespace(
                    paper_id="full-2017", title="Same Paper", doi=None, publication_year=2017, file_id="file-2017"
                ),
            ]

    _patch(monkeypatch, DuplicateRepository())

    result = await service.find_duplicate_papers(kb_id="kb-1", current_user=SimpleNamespace(uid="user-1"))

    group = result["groups"][0]
    assert group["recommended_paper_id"] == "full-2017"
    assert group["merge_preview"]["keep"]["paper_id"] == "full-2017"


@pytest.mark.asyncio
async def test_find_duplicates_groups_doi_and_title_only_records(monkeypatch):
    class DuplicateRepository:
        async def iter_export_batches(self, *, kb_id, batch_size=200):
            yield [
                SimpleNamespace(
                    paper_id="doi-only",
                    title="Same Paper",
                    doi="10.1/x",
                    publication_year=2024,
                    file_id=None,
                ),
                SimpleNamespace(
                    paper_id="full-title-only",
                    title="Same Paper",
                    doi=None,
                    publication_year=2024,
                    file_id="file-1",
                ),
            ]

    _patch(monkeypatch, DuplicateRepository())

    result = await service.find_duplicate_papers(kb_id="kb-1", current_user=SimpleNamespace(uid="user-1"))

    assert result["duplicate_group_count"] == 1
    group = result["groups"][0]
    assert group["count"] == 2
    assert group["recommended_paper_id"] == "full-title-only"
