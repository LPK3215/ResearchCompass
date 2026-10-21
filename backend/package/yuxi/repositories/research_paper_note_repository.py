"""ResearchCompass 论文笔记与批注数据访问层。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_knowledge import ResearchPaperNote


class ResearchPaperNoteRepository:
    async def list_notes(self, *, kb_id: str, paper_id: str, uid: str) -> list[ResearchPaperNote]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(ResearchPaperNote)
                .where(
                    ResearchPaperNote.kb_id == kb_id,
                    ResearchPaperNote.paper_id == paper_id,
                    ResearchPaperNote.uid == uid,
                )
                .order_by(ResearchPaperNote.created_at.asc(), ResearchPaperNote.id.asc())
            )
            return list(result.scalars().all())

    async def get_note(self, note_id: str) -> ResearchPaperNote | None:
        async with pg_manager.get_async_session_context() as session:
            return await session.scalar(select(ResearchPaperNote).where(ResearchPaperNote.note_id == note_id))

    async def create_note(self, data: dict[str, Any]) -> ResearchPaperNote:
        async with pg_manager.get_async_session_context() as session:
            note = ResearchPaperNote(**data)
            session.add(note)
            await session.flush()
            return note

    async def update_note_content(self, *, note_id: str, uid: str, content: str) -> ResearchPaperNote | None:
        async with pg_manager.get_async_session_context() as session:
            note = await session.scalar(
                select(ResearchPaperNote).where(
                    ResearchPaperNote.note_id == note_id,
                    ResearchPaperNote.uid == uid,
                )
            )
            if note is None:
                return None
            note.content = content
            await session.flush()
            return note

    async def delete_note(self, *, note_id: str, uid: str) -> bool:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                delete(ResearchPaperNote).where(
                    ResearchPaperNote.note_id == note_id,
                    ResearchPaperNote.uid == uid,
                )
            )
            return bool(result.rowcount)
