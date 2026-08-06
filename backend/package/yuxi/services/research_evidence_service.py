from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from yuxi.repositories.knowledge_chunk_repository import KnowledgeChunkRepository
from yuxi.services.research_paper_service import _ensure_access
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_knowledge import (
    ResearchEvidence,
    ResearchEvidenceActivity,
    ResearchEvidenceCitation,
    ResearchProject,
    ResearchProjectAsset,
    ResearchProjectMilestone,
    ResearchProjectPlanAssetLink,
    ResearchProjectTask,
)
from yuxi.storage.postgres.models_knowledge import ResearchSynthesisRun


ALLOWED_TRANSITIONS = {
    "unverified": {"verified", "invalid", "archived"},
    "verified": {"invalid", "archived"},
    "invalid": {"archived"},
    "archived": set(),
}


async def create_evidence(*, kb_id: str, chunk_id: str, current_user: User) -> dict:
    await _ensure_access(current_user, kb_id)
    chunks = await KnowledgeChunkRepository().list_by_chunk_ids([chunk_id])
    if not chunks or str(chunks[0].kb_id) != str(kb_id):
        raise HTTPException(status_code=404, detail="证据来源不存在")
    chunk = chunks[0]
    async with pg_manager.get_async_session_context() as session:
        row = ResearchEvidence(evidence_id=uuid.uuid4().hex, kb_id=kb_id, source_chunk_id=chunk_id,
                               content_snapshot=str(chunk.content or ""), created_by=str(current_user.uid))
        session.add(row)
        session.add(ResearchEvidenceActivity(
            activity_id=uuid.uuid4().hex,
            evidence_id=row.evidence_id,
            operator_uid=str(current_user.uid),
            from_status="none",
            to_status="unverified",
            reason="evidence_created",
        ))
        try:
            await session.flush()
        except IntegrityError as exc:
            raise HTTPException(status_code=409, detail="该知识片段已经录入为证据") from exc
        return serialize_evidence(row)


async def transition_evidence(*, evidence_id: str, status: str, reason: str | None, current_user: User) -> dict:
    if status not in {"verified", "invalid", "archived"}:
        raise HTTPException(status_code=422, detail="不支持的证据状态")
    async with pg_manager.get_async_session_context() as session:
        row = await session.scalar(
            select(ResearchEvidence).where(ResearchEvidence.evidence_id == evidence_id).with_for_update()
        )
        if row is None:
            raise HTTPException(status_code=404, detail="证据不存在")
        await _ensure_access(current_user, str(row.kb_id))
        if status not in ALLOWED_TRANSITIONS.get(str(row.status), set()):
            raise HTTPException(status_code=409, detail=f"证据状态不能从 {row.status} 转换为 {status}")
        previous_status = str(row.status)
        row.status = status
        if status == "verified":
            row.verified_by, row.verified_at = str(current_user.uid), datetime.now(UTC).replace(tzinfo=None)
        if status == "invalid":
            row.invalid_reason = reason
        if status == "archived":
            row.archived_by, row.archived_at = str(current_user.uid), datetime.now(UTC).replace(tzinfo=None)
        session.add(ResearchEvidenceActivity(
            activity_id=uuid.uuid4().hex,
            evidence_id=row.evidence_id,
            operator_uid=str(current_user.uid),
            from_status=previous_status,
            to_status=status,
            reason=reason,
        ))
        await session.flush()
        return serialize_evidence(row)


async def list_evidence_activities(*, evidence_id: str, current_user: User) -> list[dict]:
    async with pg_manager.get_async_session_context() as session:
        row = await session.scalar(select(ResearchEvidence).where(ResearchEvidence.evidence_id == evidence_id))
        if row is None:
            raise HTTPException(status_code=404, detail="证据不存在")
        await _ensure_access(current_user, str(row.kb_id))
        result = await session.execute(
            select(ResearchEvidenceActivity)
            .where(ResearchEvidenceActivity.evidence_id == evidence_id)
            .order_by(ResearchEvidenceActivity.created_at.asc(), ResearchEvidenceActivity.id.asc())
        )
        return [
            {"activity_id": item.activity_id, "operator_uid": item.operator_uid,
             "from_status": item.from_status, "to_status": item.to_status,
             "reason": item.reason, "created_at": item.created_at.isoformat() if item.created_at else None}
            for item in result.scalars().all()
        ]


async def list_evidence_citations(*, evidence_id: str, current_user: User) -> list[dict]:
    """List immutable synthesis citations for an evidence item with ownership checks."""
    async with pg_manager.get_async_session_context() as session:
        evidence = await session.scalar(select(ResearchEvidence).where(ResearchEvidence.evidence_id == evidence_id))
        if evidence is None:
            raise HTTPException(status_code=404, detail="证据不存在")
        await _ensure_access(current_user, str(evidence.kb_id))
        result = await session.execute(
            select(ResearchEvidenceCitation, ResearchSynthesisRun)
            .join(
                ResearchSynthesisRun,
                ResearchSynthesisRun.run_id == ResearchEvidenceCitation.synthesis_run_id,
            )
            .where(ResearchEvidenceCitation.evidence_id == evidence_id)
            .order_by(ResearchEvidenceCitation.cited_at.asc(), ResearchEvidenceCitation.id.asc())
        )
        return [
            {
                "citation_id": citation.citation_id,
                "evidence_id": citation.evidence_id,
                "synthesis_run_id": citation.synthesis_run_id,
                "synthesis_status": synthesis.status,
                "cited_by": citation.cited_by,
                "cited_at": citation.cited_at.isoformat() if citation.cited_at else None,
            }
            for citation, synthesis in result.all()
        ]


async def list_evidence_impacts(*, evidence_id: str, current_user: User) -> list[dict]:
    """Return project execution items that currently depend on an evidence asset."""
    async with pg_manager.get_async_session_context() as session:
        evidence = await session.scalar(select(ResearchEvidence).where(ResearchEvidence.evidence_id == evidence_id))
        if evidence is None:
            raise HTTPException(status_code=404, detail="证据不存在")
        await _ensure_access(current_user, str(evidence.kb_id))
        result = await session.execute(
            select(
                ResearchProject.project_id,
                ResearchProject.title,
                ResearchProjectPlanAssetLink.link_id,
                ResearchProjectMilestone.milestone_id,
                ResearchProjectMilestone.title,
                ResearchProjectTask.task_id,
                ResearchProjectTask.title,
            )
            .join(ResearchProjectAsset, ResearchProjectAsset.project_id == ResearchProject.project_id)
            .join(
                ResearchProjectPlanAssetLink,
                ResearchProjectPlanAssetLink.asset_id == ResearchProjectAsset.asset_id,
            )
            .outerjoin(
                ResearchProjectMilestone,
                ResearchProjectMilestone.milestone_id == ResearchProjectPlanAssetLink.milestone_id,
            )
            .outerjoin(ResearchProjectTask, ResearchProjectTask.task_id == ResearchProjectPlanAssetLink.task_id)
            .where(
                ResearchProjectAsset.asset_type == "evidence",
                ResearchProjectAsset.reference_id == evidence_id,
                ResearchProject.uid == str(current_user.uid),
            )
            .order_by(ResearchProjectPlanAssetLink.id.asc())
        )
        return [
            {
                "project_id": project_id,
                "project_title": project_title,
                "link_id": link_id,
                "target_type": "milestone" if milestone_id else "task",
                "target_id": milestone_id or task_id,
                "target_title": milestone_title or task_title,
            }
            for project_id, project_title, link_id, milestone_id, milestone_title, task_id, task_title in result.all()
        ]


async def list_evidence(*, kb_id: str, status: str | None, current_user: User) -> list[dict]:
    await _ensure_access(current_user, kb_id)
    async with pg_manager.get_async_session_context() as session:
        statement = select(ResearchEvidence).where(ResearchEvidence.kb_id == kb_id)
        if status:
            statement = statement.where(ResearchEvidence.status == status)
        result = await session.execute(statement.order_by(ResearchEvidence.created_at.desc()))
        return [serialize_evidence(row) for row in result.scalars().all()]


def serialize_evidence(row: ResearchEvidence) -> dict:
    return {"evidence_id": row.evidence_id, "kb_id": row.kb_id, "source_chunk_id": row.source_chunk_id,
            "content": row.content_snapshot, "status": row.status, "invalid_reason": row.invalid_reason,
            "created_by": row.created_by, "verified_by": row.verified_by,
            "verified_at": row.verified_at.isoformat() if row.verified_at else None,
            "archived_by": row.archived_by, "archived_at": row.archived_at.isoformat() if row.archived_at else None}
