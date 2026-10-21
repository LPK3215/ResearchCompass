"""知识库迁移包（ZIP）的内容与图谱空降级测试。"""

import io
import json
import zipfile
from types import SimpleNamespace

import pytest

from yuxi.services import research_deliverable_service as deliverable


def _file(file_id, filename, *, is_folder=False, status="indexed", chunk_count=10):
    return SimpleNamespace(
        file_id=file_id,
        filename=filename,
        original_filename=filename,
        file_type="pdf",
        status=status,
        file_size=1024,
        chunk_count=chunk_count,
        minio_url=f"minio://{file_id}",
        path=None,
        markdown_file=f"{file_id}.md",
        is_folder=is_folder,
    )


class FakeFileRepository:
    def __init__(self, records):
        self.records = records

    async def list_by_kb_id(self, kb_id):
        return self.records


MANIFEST = {
    "kb_id": "kb-1",
    "generated_at": "2026-10-21 10:00",
    "paper_count": 2,
    "papers": [
        {
            "paper_id": "p1",
            "file_id": "f1",
            "title": "=SUM(A1)",
            "publication_year": 2024,
            "doi": "10.1/a",
            "venue": "NeurIPS",
        },
        {
            "paper_id": "p2",
            "file_id": "f2",
            "title": "Second",
            "publication_year": 2025,
            "doi": "10.1/b",
            "venue": "ICML",
        },
    ],
    "graph_counts": {"papers": 2, "citations": 1},
    "note": "test",
}


@pytest.fixture
def allow_access(monkeypatch):
    async def _allow(*args, **kwargs):
        return None

    monkeypatch.setattr(deliverable, "_ensure_access", _allow)


@pytest.mark.asyncio
async def test_bundle_contains_manifest_papers_files_and_graph(monkeypatch, allow_access):
    from yuxi.services import academic_graph_sync_service

    async def fake_manifest(**kwargs):
        return MANIFEST

    async def fake_graph(**kwargs):
        return "graph.csv", b"node_id,label\nn1,Paper\n", "text/csv; charset=utf-8"

    monkeypatch.setattr(deliverable, "export_kb_manifest", fake_manifest)
    monkeypatch.setattr(
        "yuxi.repositories.knowledge_file_repository.KnowledgeFileRepository",
        lambda: FakeFileRepository([_file("f1", "first.pdf"), _file("folder-1", "sub", is_folder=True)]),
    )
    monkeypatch.setattr(academic_graph_sync_service, "export_academic_graph", fake_graph)

    filename, content, media_type = await deliverable.export_kb_bundle(
        kb_id="kb-1", current_user=SimpleNamespace(uid="user-1")
    )

    assert filename.startswith("research_bundle_kb-1_")
    assert filename.endswith(".zip")
    assert media_type == "application/zip"

    archive = zipfile.ZipFile(io.BytesIO(content))
    names = set(archive.namelist())
    assert names == {"manifest.json", "papers.csv", "files.csv", "graph/citation_graph.csv", "README.md"}
    assert json.loads(archive.read("manifest.json"))["paper_count"] == 2

    papers_csv = archive.read("papers.csv").decode("utf-8-sig")
    assert "'=SUM(A1)" in papers_csv
    assert "Second" in papers_csv

    files_csv = archive.read("files.csv").decode("utf-8-sig")
    assert "first.pdf" in files_csv
    assert "folder-1" not in files_csv

    assert "node_id" in archive.read("graph/citation_graph.csv").decode("utf-8")
    assert "迁移包" in archive.read("README.md").decode("utf-8")


@pytest.mark.asyncio
async def test_bundle_keeps_going_when_graph_is_empty(monkeypatch, allow_access):
    from yuxi.services import academic_graph_sync_service

    async def fake_manifest(**kwargs):
        return MANIFEST

    async def empty_graph(**kwargs):
        raise academic_graph_sync_service.AcademicGraphSyncError("graph_empty", "引用图谱为空")

    monkeypatch.setattr(deliverable, "export_kb_manifest", fake_manifest)
    monkeypatch.setattr(
        "yuxi.repositories.knowledge_file_repository.KnowledgeFileRepository",
        lambda: FakeFileRepository([]),
    )
    monkeypatch.setattr(academic_graph_sync_service, "export_academic_graph", empty_graph)

    _, content, _ = await deliverable.export_kb_bundle(kb_id="kb-1", current_user=SimpleNamespace(uid="user-1"))

    archive = zipfile.ZipFile(io.BytesIO(content))
    graph_csv = archive.read("graph/citation_graph.csv").decode("utf-8")
    assert graph_csv.startswith("notice")
    assert "图谱尚未同步" in graph_csv
