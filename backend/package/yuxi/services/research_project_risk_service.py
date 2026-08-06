"""研究项目风险登记与处置服务。"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select

from yuxi.services.research_project_service import ResearchProjectError, ensure_project_writable, get_owned_project
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_knowledge import ResearchProjectRisk, ResearchProjectRiskActivity
from yuxi.utils.datetime_utils import utc_now_naive

SEVERITIES = {"low", "medium", "high", "critical"}
STATUSES = {"open", "mitigating", "accepted", "resolved", "closed"}
ALLOWED_TRANSITIONS = {
    "open": {"mitigating", "accepted", "resolved", "closed"},
    "mitigating": {"accepted", "resolved", "open", "closed"},
    "accepted": {"mitigating", "resolved", "closed"},
    "resolved": {"closed", "open"},
    "closed": set(),
}


def serialize_risk(risk: ResearchProjectRisk) -> dict:
    return {
        "risk_id": risk.risk_id, "project_id": risk.project_id, "title": risk.title,
        "description": risk.description, "severity": risk.severity, "status": risk.status,
        "owner_uid": risk.owner_uid, "mitigation": risk.mitigation, "due_date": risk.due_date.isoformat() if risk.due_date else None,
        "created_by": risk.created_by, "resolved_at": risk.resolved_at.isoformat() if risk.resolved_at else None,
        "created_at": risk.created_at.isoformat() if risk.created_at else None, "updated_at": risk.updated_at.isoformat() if risk.updated_at else None,
    }


async def list_project_risks(*, project_id: str, current_user: User) -> list[dict]:
    await get_owned_project(project_id, current_user)
    async with pg_manager.get_async_session_context() as session:
        rows = await session.scalars(select(ResearchProjectRisk).where(ResearchProjectRisk.project_id == project_id).order_by(ResearchProjectRisk.updated_at.desc(), ResearchProjectRisk.id.desc()))
        return [serialize_risk(row) for row in rows]


async def create_project_risk(*, project_id: str, current_user: User, title: str, description: str, severity: str, owner_uid: str, mitigation: str, due_date: date | None) -> dict:
    project = await get_owned_project(project_id, current_user)
    ensure_project_writable(project)
    if severity not in SEVERITIES:
        raise ValueError("不支持的风险等级")
    async with pg_manager.get_async_session_context() as session:
        owner = await session.scalar(select(User).where(User.uid == owner_uid, User.is_deleted == 0))
        if owner is None:
            raise ValueError("风险责任人不存在")
        row = ResearchProjectRisk(risk_id=uuid.uuid4().hex, project_id=project_id, title=title, description=description, severity=severity, owner_uid=owner_uid, mitigation=mitigation, due_date=due_date, created_by=str(current_user.uid))
        session.add(row)
        session.add(ResearchProjectRiskActivity(activity_id=uuid.uuid4().hex, risk_id=row.risk_id, project_id=project_id, operator_uid=str(current_user.uid), activity_type="created", to_status="open", changes={"title": title, "severity": severity, "owner_uid": owner_uid}, precondition={"project_status": project.status}))
        await session.flush()
    return serialize_risk(row)


async def transition_project_risk(*, project_id: str, risk_id: str, status: str, current_user: User, mitigation: str | None = None, reason: str | None = None) -> dict:
    project = await get_owned_project(project_id, current_user)
    ensure_project_writable(project)
    if status not in STATUSES:
        raise ValueError("不支持的风险状态")
    async with pg_manager.get_async_session_context() as session:
        row = await session.scalar(select(ResearchProjectRisk).where(ResearchProjectRisk.project_id == project_id, ResearchProjectRisk.risk_id == risk_id).with_for_update())
        if row is None:
            raise ResearchProjectError("risk_not_found", "风险不存在")
        if status not in ALLOWED_TRANSITIONS[row.status]:
            raise RuntimeError(f"风险状态不能从 {row.status} 转换为 {status}")
        previous = row.status
        row.status = status
        if mitigation is not None:
            row.mitigation = mitigation
        row.resolved_at = utc_now_naive() if status in {"resolved", "closed"} else None
        session.add(ResearchProjectRiskActivity(activity_id=uuid.uuid4().hex, risk_id=risk_id, project_id=project_id, operator_uid=str(current_user.uid), activity_type="transitioned", from_status=previous, to_status=status, changes={"mitigation": mitigation, "reason": reason}, precondition={"previous_status": previous}))
        await session.flush()
        return serialize_risk(row)


async def list_project_risk_activities(*, project_id: str, risk_id: str, current_user: User) -> list[dict]:
    await get_owned_project(project_id, current_user)
    async with pg_manager.get_async_session_context() as session:
        risk_exists = await session.scalar(
            select(ResearchProjectRisk.risk_id).where(
                ResearchProjectRisk.project_id == project_id,
                ResearchProjectRisk.risk_id == risk_id,
            )
        )
        if risk_exists is None:
            raise ResearchProjectError("risk_not_found", "风险不存在")
        rows = await session.scalars(select(ResearchProjectRiskActivity).where(ResearchProjectRiskActivity.project_id == project_id, ResearchProjectRiskActivity.risk_id == risk_id).order_by(ResearchProjectRiskActivity.created_at.asc(), ResearchProjectRiskActivity.id.asc()))
        return [{"activity_id": row.activity_id, "operator_uid": row.operator_uid, "activity_type": row.activity_type, "from_status": row.from_status, "to_status": row.to_status, "changes": row.changes, "precondition": row.precondition, "created_at": row.created_at.isoformat() if row.created_at else None} for row in rows]
