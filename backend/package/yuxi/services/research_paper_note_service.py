"""ResearchCompass 论文阅读笔记与选区批注服务。

笔记按用户隔离，绑定到知识库内的单篇论文；选区批注额外记录分块、字符区间与
PDF 页码。导出只覆盖当前用户自己的笔记，不改变论文、证据或综述数据。
"""

from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

from yuxi.repositories.academic_paper_repository import AcademicPaperRepository
from yuxi.repositories.knowledge_chunk_repository import KnowledgeChunkRepository
from yuxi.repositories.research_paper_note_repository import ResearchPaperNoteRepository
from yuxi.services.research_paper_service import _ensure_access
from yuxi.storage.postgres.models_business import User
from yuxi.utils import hashstr

MAX_NOTE_CONTENT_CHARS = 10_000
MAX_NOTE_QUOTE_CHARS = 5_000


class ResearchPaperNoteError(ValueError):
    def __init__(self, error_type: str, message: str):
        super().__init__(message)
        self.error_type = error_type
        self.message = message


async def list_paper_notes(*, kb_id: str, paper_id: str, current_user: User) -> dict[str, Any]:
    await _ensure_access(current_user, kb_id)
    await _get_paper(kb_id=kb_id, paper_id=paper_id)
    notes = await ResearchPaperNoteRepository().list_notes(kb_id=kb_id, paper_id=paper_id, uid=str(current_user.uid))
    return {"paper_id": paper_id, "count": len(notes), "items": [_serialize_note(note) for note in notes]}


async def create_paper_note(
    *,
    kb_id: str,
    paper_id: str,
    content: str,
    quote: str | None,
    chunk_id: str | None,
    page: int | None,
    start_offset: int | None,
    end_offset: int | None,
    current_user: User,
) -> dict[str, Any]:
    await _ensure_access(current_user, kb_id)
    paper = await _get_paper(kb_id=kb_id, paper_id=paper_id)
    normalized_content = _normalize_content(content)
    normalized_quote = _normalize_quote(quote)
    resolved_page = await _validate_annotation(
        paper=paper,
        chunk_id=chunk_id,
        start_offset=start_offset,
        end_offset=end_offset,
    )
    note = await ResearchPaperNoteRepository().create_note(
        {
            "note_id": f"note_{hashstr(f'{kb_id}:{paper_id}:{current_user.uid}:{uuid4().hex}', 24)}",
            "kb_id": kb_id,
            "paper_id": paper_id,
            "uid": str(current_user.uid),
            "content": normalized_content,
            "quote": normalized_quote,
            "chunk_id": chunk_id or None,
            "page": page if page and page > 0 else resolved_page,
            "start_offset": start_offset if chunk_id else None,
            "end_offset": end_offset if chunk_id else None,
        }
    )
    return _serialize_note(note)


async def update_paper_note(
    *, note_id: str, content: str, current_user: User, paper_id: str | None = None
) -> dict[str, Any]:
    repo = ResearchPaperNoteRepository()
    note = await repo.get_note(note_id)
    if note is None or (paper_id and str(note.paper_id) != str(paper_id)):
        raise ResearchPaperNoteError("note_not_found", "笔记不存在")
    await _ensure_access(current_user, note.kb_id)
    if str(note.uid) != str(current_user.uid):
        raise ResearchPaperNoteError("note_forbidden", "只能修改自己的笔记")
    updated = await repo.update_note_content(
        note_id=note_id,
        uid=str(current_user.uid),
        content=_normalize_content(content),
    )
    if updated is None:
        raise ResearchPaperNoteError("note_not_found", "笔记不存在")
    return _serialize_note(updated)


async def delete_paper_note(*, note_id: str, current_user: User, paper_id: str | None = None) -> dict[str, Any]:
    repo = ResearchPaperNoteRepository()
    note = await repo.get_note(note_id)
    if note is None or (paper_id and str(note.paper_id) != str(paper_id)):
        raise ResearchPaperNoteError("note_not_found", "笔记不存在")
    await _ensure_access(current_user, note.kb_id)
    if str(note.uid) != str(current_user.uid):
        raise ResearchPaperNoteError("note_forbidden", "只能删除自己的笔记")
    deleted = await repo.delete_note(note_id=note_id, uid=str(current_user.uid))
    if not deleted:
        raise ResearchPaperNoteError("note_not_found", "笔记不存在")
    return {"deleted": True, "note_id": note_id}


async def export_paper_notes_markdown(*, kb_id: str, paper_id: str, current_user: User) -> tuple[str, bytes, str]:
    await _ensure_access(current_user, kb_id)
    paper = await _get_paper(kb_id=kb_id, paper_id=paper_id)
    notes = await ResearchPaperNoteRepository().list_notes(kb_id=kb_id, paper_id=paper_id, uid=str(current_user.uid))
    lines = [f"# {paper.title} · 阅读笔记", ""]
    if not notes:
        lines.append("_暂无笔记_")
    for index, note in enumerate(notes, start=1):
        lines.append(f"## 笔记 {index}")
        meta = []
        if note.page:
            meta.append(f"PDF 第 {note.page} 页")
        if note.created_at:
            meta.append(note.created_at.strftime("%Y-%m-%d %H:%M"))
        if meta:
            lines.extend([" · ".join(meta), ""])
        if note.quote:
            lines.extend(["> " + str(note.quote).replace("\n", "\n> "), ""])
        lines.extend([str(note.content), ""])
    return (
        f"paper_notes_{re.sub(r'[^A-Za-z0-9_-]', '_', paper_id)}.md",
        "\n".join(lines).encode("utf-8"),
        "text/markdown; charset=utf-8",
    )


async def _get_paper(*, kb_id: str, paper_id: str):
    paper = await AcademicPaperRepository().get_by_paper_id(kb_id=kb_id, paper_id=paper_id)
    if paper is None:
        raise ResearchPaperNoteError("paper_not_found", "论文不存在")
    return paper


async def _validate_annotation(
    *, paper, chunk_id: str | None, start_offset: int | None, end_offset: int | None
) -> int | None:
    if not chunk_id:
        return None
    chunk = await KnowledgeChunkRepository().get_by_chunk_id(chunk_id)
    if chunk is None or paper.file_id is None or chunk.file_id != paper.file_id:
        raise ResearchPaperNoteError("note_annotation_invalid", "批注对应的论文分块不存在")
    content = str(chunk.content or "")
    if start_offset is None or end_offset is None or not (0 <= start_offset < end_offset <= len(content)):
        raise ResearchPaperNoteError("note_annotation_invalid", "批注选区超出分块范围")
    metadata = chunk.chunk_metadata if isinstance(chunk.chunk_metadata, dict) else {}
    page = metadata.get("source_page_start")
    return int(page) if isinstance(page, int) and page > 0 else None


def _normalize_content(content: str | None) -> str:
    normalized = (content or "").strip()
    if not normalized:
        raise ResearchPaperNoteError("note_content_invalid", "笔记内容不能为空")
    if len(normalized) > MAX_NOTE_CONTENT_CHARS:
        raise ResearchPaperNoteError("note_content_invalid", "笔记内容超过长度限制")
    return normalized


def _normalize_quote(quote: str | None) -> str | None:
    normalized = re.sub(r"\s+", " ", (quote or "").strip())
    if not normalized:
        return None
    return normalized[:MAX_NOTE_QUOTE_CHARS]


def _serialize_note(note) -> dict[str, Any]:
    return {
        "note_id": note.note_id,
        "paper_id": note.paper_id,
        "content": note.content,
        "quote": note.quote or "",
        "chunk_id": note.chunk_id,
        "page": note.page,
        "start_offset": note.start_offset,
        "end_offset": note.end_offset,
        "created_at": note.created_at.isoformat() if note.created_at else None,
        "updated_at": note.updated_at.isoformat() if note.updated_at else None,
    }
