from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, func, select

from yuxi.repositories.research_user_study_repository import ResearchUserStudyRepository
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_knowledge import (
    KnowledgeBase,
    ResearchUserStudy,
    ResearchUserStudyInvite,
    ResearchUserStudyResponse,
)
from yuxi.utils.datetime_utils import utc_now_naive


pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_response_creation_rechecks_closed_study_in_write_transaction():
    suffix = uuid.uuid4().hex
    kb_id = f"pytest_kb_{suffix}"
    study_id = f"study_{suffix}"
    invite_id = f"invite_{suffix}"
    async with pg_manager.get_async_session_context() as session:
        session.add(KnowledgeBase(kb_id=kb_id, name=kb_id, kb_type="milvus"))
        await session.flush()
        session.add(
            ResearchUserStudy(
                study_id=study_id,
                kb_id=kb_id,
                name="closed study",
                consent_text="consent",
                status="closed",
                created_by="pytest",
            )
        )
        await session.flush()
        session.add(
            ResearchUserStudyInvite(
                invite_id=invite_id,
                study_id=study_id,
                token_hash=uuid.uuid4().hex,
                status="pending",
            )
        )

    try:
        response, rejection = await ResearchUserStudyRepository().create_response_and_use_invite(
            study_id=study_id,
            invite_id=invite_id,
            response_data={
                "response_id": f"response_{suffix}",
                "study_id": study_id,
                "invite_id": invite_id,
                "research_stage": "master",
                "research_experience": "1_to_3_years",
                "task_scores": {"search": 4},
                "sus_scores": {"q1": 3},
                "overall_rating": 4,
                "recommend_score": 8,
                "submitted_at": utc_now_naive(),
            },
        )

        assert response is None
        assert rejection == "study_closed"
        async with pg_manager.get_async_session_context() as session:
            response_count = await session.scalar(
                select(func.count())
                .select_from(ResearchUserStudyResponse)
                .where(ResearchUserStudyResponse.study_id == study_id)
            )
            invite = await session.scalar(
                select(ResearchUserStudyInvite).where(ResearchUserStudyInvite.invite_id == invite_id)
            )
        assert response_count == 0
        assert invite.status == "pending"
    finally:
        async with pg_manager.get_async_session_context() as session:
            await session.execute(delete(KnowledgeBase).where(KnowledgeBase.kb_id == kb_id))
