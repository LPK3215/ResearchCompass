"""research_paper_note_service 单元测试。

只验证笔记归属、内容归一化、批注范围校验与 Markdown 导出，不依赖数据库。
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import yuxi.services.research_paper_note_service as note_service
from yuxi.services.research_paper_note_service import (
    ResearchPaperNoteError,
    create_paper_note,
    delete_paper_note,
    export_paper_notes_markdown,
    list_paper_notes,
    update_paper_note,
)

PAPER = SimpleNamespace(paper_id="p1", file_id="f1", title="Test Paper")
CHUNK = SimpleNamespace(
    chunk_id="c1",
    file_id="f1",
    content="abcdefghij",
    chunk_metadata={"source_page_start": 3},
)


class FakeNoteRepository:
    def __init__(self, notes: list[SimpleNamespace] | None = None):
        self.notes = notes or []
        self.created: dict | None = None

    async def list_notes(self, *, kb_id: str, paper_id: str, uid: str):
        return [
            note
            for note in self.notes
            if note.kb_id == kb_id and note.paper_id == paper_id and note.uid == uid
        ]

    async def get_note(self, note_id: str):
        return next((note for note in self.notes if note.note_id == note_id), None)

    async def create_note(self, data: dict):
        self.created = data
        note = SimpleNamespace(
            **data,
            created_at=datetime(2026, 10, 21, 12, 0),
            updated_at=datetime(2026, 10, 21, 12, 0),
        )
        self.notes.append(note)
        return note

    async def update_note_content(self, *, note_id: str, uid: str, content: str):
        note = await self.get_note(note_id)
        if note is None or note.uid != uid:
            return None
        note.content = content
        return note

    async def delete_note(self, *, note_id: str, uid: str):
        note = await self.get_note(note_id)
        if note is None or note.uid != uid:
            return False
        self.notes.remove(note)
        return True


def _patch(monkeypatch, *, repo: FakeNoteRepository, chunk=CHUNK):
    monkeypatch.setattr(note_service, "_ensure_access", AsyncMock())
    monkeypatch.setattr(
        note_service,
        "AcademicPaperRepository",
        lambda: SimpleNamespace(get_by_paper_id=AsyncMock(return_value=PAPER)),
    )
    monkeypatch.setattr(
        note_service,
        "KnowledgeChunkRepository",
        lambda: SimpleNamespace(get_by_chunk_id=AsyncMock(return_value=chunk)),
    )
    monkeypatch.setattr(note_service, "ResearchPaperNoteRepository", lambda: repo)


class TestCreatePaperNote:
    async def test_normalizes_content_and_quote(self, monkeypatch):
        repo = FakeNoteRepository()
        _patch(monkeypatch, repo=repo)

        result = await create_paper_note(
            kb_id="kb",
            paper_id="p1",
            content="  My note  ",
            quote="  quoted \n text ",
            chunk_id=None,
            page=None,
            start_offset=None,
            end_offset=None,
            current_user=SimpleNamespace(uid="u1"),
        )

        assert result["content"] == "My note"
        assert result["quote"] == "quoted text"
        assert repo.created["uid"] == "u1"
        assert repo.created["page"] is None

    async def test_empty_content_rejected(self, monkeypatch):
        _patch(monkeypatch, repo=FakeNoteRepository())
        with pytest.raises(ResearchPaperNoteError) as exc_info:
            await create_paper_note(
                kb_id="kb",
                paper_id="p1",
                content="   ",
                quote=None,
                chunk_id=None,
                page=None,
                start_offset=None,
                end_offset=None,
                current_user=SimpleNamespace(uid="u1"),
            )
        assert exc_info.value.error_type == "note_content_invalid"

    async def test_annotation_uses_chunk_page(self, monkeypatch):
        repo = FakeNoteRepository()
        _patch(monkeypatch, repo=repo)

        result = await create_paper_note(
            kb_id="kb",
            paper_id="p1",
            content="note",
            quote="bcd",
            chunk_id="c1",
            page=None,
            start_offset=1,
            end_offset=4,
            current_user=SimpleNamespace(uid="u1"),
        )

        assert result["page"] == 3
        assert result["chunk_id"] == "c1"
        assert repo.created["start_offset"] == 1
        assert repo.created["end_offset"] == 4

    async def test_invalid_annotation_rejected(self, monkeypatch):
        _patch(monkeypatch, repo=FakeNoteRepository())
        with pytest.raises(ResearchPaperNoteError) as exc_info:
            await create_paper_note(
                kb_id="kb",
                paper_id="p1",
                content="note",
                quote="x",
                chunk_id="c1",
                page=None,
                start_offset=8,
                end_offset=2,
                current_user=SimpleNamespace(uid="u1"),
            )
        assert exc_info.value.error_type == "note_annotation_invalid"


class TestUpdateAndDelete:
    async def test_update_rejects_other_user(self, monkeypatch):
        repo = FakeNoteRepository(
            [
                SimpleNamespace(
                    note_id="n1",
                    kb_id="kb",
                    paper_id="p1",
                    uid="u2",
                    content="old",
                    quote=None,
                    chunk_id=None,
                    page=None,
                    start_offset=None,
                    end_offset=None,
                    created_at=None,
                    updated_at=None,
                )
            ]
        )
        _patch(monkeypatch, repo=repo)
        with pytest.raises(ResearchPaperNoteError) as exc_info:
            await update_paper_note(note_id="n1", content="new", current_user=SimpleNamespace(uid="u1"))
        assert exc_info.value.error_type == "note_forbidden"

    async def test_delete_own_note(self, monkeypatch):
        repo = FakeNoteRepository(
            [
                SimpleNamespace(
                    note_id="n1",
                    kb_id="kb",
                    paper_id="p1",
                    uid="u1",
                    content="old",
                    quote=None,
                    chunk_id=None,
                    page=None,
                    start_offset=None,
                    end_offset=None,
                    created_at=None,
                    updated_at=None,
                )
            ]
        )
        _patch(monkeypatch, repo=repo)
        result = await delete_paper_note(note_id="n1", current_user=SimpleNamespace(uid="u1"))
        assert result == {"deleted": True, "note_id": "n1"}
        assert repo.notes == []


class TestListAndExport:
    async def test_list_only_returns_current_user_notes(self, monkeypatch):
        repo = FakeNoteRepository(
            [
                SimpleNamespace(
                    note_id="n1",
                    kb_id="kb",
                    paper_id="p1",
                    uid="u1",
                    content="mine",
                    quote="",
                    chunk_id=None,
                    page=None,
                    start_offset=None,
                    end_offset=None,
                    created_at=None,
                    updated_at=None,
                ),
                SimpleNamespace(
                    note_id="n2",
                    kb_id="kb",
                    paper_id="p1",
                    uid="u2",
                    content="other",
                    quote="",
                    chunk_id=None,
                    page=None,
                    start_offset=None,
                    end_offset=None,
                    created_at=None,
                    updated_at=None,
                ),
            ]
        )
        _patch(monkeypatch, repo=repo)
        result = await list_paper_notes(kb_id="kb", paper_id="p1", current_user=SimpleNamespace(uid="u1"))
        assert result["count"] == 1
        assert result["items"][0]["content"] == "mine"

    async def test_export_contains_page_and_quote(self, monkeypatch):
        repo = FakeNoteRepository(
            [
                SimpleNamespace(
                    note_id="n1",
                    kb_id="kb",
                    paper_id="p1",
                    uid="u1",
                    content="important",
                    quote="quoted text",
                    chunk_id="c1",
                    page=3,
                    start_offset=1,
                    end_offset=4,
                    created_at=datetime(2026, 10, 21, 12, 0),
                    updated_at=datetime(2026, 10, 21, 12, 0),
                )
            ]
        )
        _patch(monkeypatch, repo=repo)
        filename, content, media_type = await export_paper_notes_markdown(
            kb_id="kb", paper_id="p1", current_user=SimpleNamespace(uid="u1")
        )
        assert filename == "paper_notes_p1.md"
        assert media_type.startswith("text/markdown")
        text = content.decode("utf-8")
        assert "# Test Paper · 阅读笔记" in text
        assert "> quoted text" in text
        assert "PDF 第 3 页" in text
        assert "important" in text
