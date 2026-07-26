from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_knowledge import (
    ResearchUserStudy,
    ResearchUserStudyInvite,
    ResearchUserStudyResponse,
)


class ResearchUserStudyRepository:
    async def create_study_with_invites(
        self, study_data: dict[str, Any], invites_data: list[dict[str, Any]]
    ) -> ResearchUserStudy:
        study = ResearchUserStudy(**study_data)
        invites = [ResearchUserStudyInvite(**item) for item in invites_data]
        async with pg_manager.get_async_session_context() as session:
            session.add(study)
            session.add_all(invites)
        return study

    async def get_study(self, study_id: str) -> ResearchUserStudy | None:
        async with pg_manager.get_async_session_context() as session:
            return await session.scalar(select(ResearchUserStudy).where(ResearchUserStudy.study_id == study_id))

    async def list_studies(self, kb_id: str) -> list[ResearchUserStudy]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(ResearchUserStudy)
                .where(ResearchUserStudy.kb_id == kb_id)
                .order_by(ResearchUserStudy.created_at.desc())
            )
            return list(result.scalars().all())

    async def update_study(self, study_id: str, values: dict[str, Any]) -> ResearchUserStudy | None:
        async with pg_manager.get_async_session_context() as session:
            record = await session.scalar(select(ResearchUserStudy).where(ResearchUserStudy.study_id == study_id))
            if record is None:
                return None
            for key, value in values.items():
                setattr(record, key, value)
            return record

    async def get_invite_by_hash(self, token_hash: str) -> ResearchUserStudyInvite | None:
        async with pg_manager.get_async_session_context() as session:
            return await session.scalar(
                select(ResearchUserStudyInvite).where(ResearchUserStudyInvite.token_hash == token_hash)
            )

    async def create_response_and_use_invite(
        self,
        *,
        study_id: str,
        invite_id: str,
        response_data: dict[str, Any],
    ) -> tuple[ResearchUserStudyResponse | None, str | None]:
        async with pg_manager.get_async_session_context() as session:
            study = await session.scalar(
                select(ResearchUserStudy)
                .where(ResearchUserStudy.study_id == study_id)
                .with_for_update()
            )
            if study is None:
                return None, "study_not_found"
            if study.status != "open":
                return None, "study_closed"
            invite = await session.scalar(
                select(ResearchUserStudyInvite)
                .where(
                    ResearchUserStudyInvite.invite_id == invite_id,
                    ResearchUserStudyInvite.study_id == study_id,
                )
                .with_for_update()
            )
            if invite is None or invite.status != "pending":
                return None, "invite_used"
            response = ResearchUserStudyResponse(**response_data)
            session.add(response)
            invite.status = "submitted"
            invite.used_at = response.submitted_at
            return response, None

    async def list_invites(self, study_id: str) -> list[ResearchUserStudyInvite]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(ResearchUserStudyInvite)
                .where(ResearchUserStudyInvite.study_id == study_id)
                .order_by(ResearchUserStudyInvite.created_at.asc())
            )
            return list(result.scalars().all())

    async def count_invites_by_status(self, study_id: str) -> dict[str, int]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(ResearchUserStudyInvite.status, func.count())
                .where(ResearchUserStudyInvite.study_id == study_id)
                .group_by(ResearchUserStudyInvite.status)
            )
            return {str(status): int(count or 0) for status, count in result.all()}

    async def list_responses(
        self, study_id: str, *, offset: int = 0, limit: int = 100
    ) -> tuple[list[ResearchUserStudyResponse], int]:
        normalized_offset = max(int(offset), 0)
        normalized_limit = min(max(int(limit), 1), 500)
        async with pg_manager.get_async_session_context() as session:
            total = await session.scalar(
                select(func.count())
                .select_from(ResearchUserStudyResponse)
                .where(ResearchUserStudyResponse.study_id == study_id)
            )
            result = await session.execute(
                select(ResearchUserStudyResponse)
                .where(ResearchUserStudyResponse.study_id == study_id)
                .order_by(ResearchUserStudyResponse.submitted_at.asc())
                .offset(normalized_offset)
                .limit(normalized_limit)
            )
            return list(result.scalars().all()), int(total or 0)

    async def count_responses(self, study_id: str) -> int:
        async with pg_manager.get_async_session_context() as session:
            return int(
                await session.scalar(
                    select(func.count())
                    .select_from(ResearchUserStudyResponse)
                    .where(ResearchUserStudyResponse.study_id == study_id)
                )
                or 0
            )
