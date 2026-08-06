from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from yuxi.services import research_project_risk_service
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Department, User
from yuxi.storage.postgres.models_knowledge import (
    KnowledgeBase,
    ResearchProject,
    ResearchProjectRisk,
    ResearchProjectRiskActivity,
)
from yuxi.utils.auth_utils import AuthUtils

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _seed_risk_context() -> tuple[SimpleNamespace, ResearchProject, int, int]:
    pg_manager.initialize()
    await pg_manager.create_tables()
    suffix = uuid.uuid4().hex
    uid = f"risk-owner-{suffix}"
    kb_id = f"risk-kb-{suffix}"
    project_id = f"risk-project-{suffix}"
    async with pg_manager.get_async_session_context() as session:
        department = Department(name=f"risk-department-{suffix}")
        session.add(department)
        await session.flush()
        user = User(
            username=uid,
            uid=uid,
            password_hash=AuthUtils.hash_password("RiskTest!123"),
            role="user",
            department_id=department.id,
        )
        session.add(user)
        kb = KnowledgeBase(kb_id=kb_id, name=f"Risk KB {suffix}", kb_type="milvus", created_by=uid)
        session.add(kb)
        session.add(
            ResearchProject(
                project_id=project_id,
                kb_id=kb_id,
                uid=uid,
                title="Risk integration project",
                research_question="How are risks audited?",
                status="active",
                progress=0,
                tags=[],
            )
        )
        await session.flush()
        return SimpleNamespace(uid=uid), SimpleNamespace(project_id=project_id, kb_id=kb_id, status="active"), department.id, user.id


async def _cleanup(kb_id: str, department_id: int, user_id: int) -> None:
    async with pg_manager.get_async_session_context() as session:
        kb = await session.scalar(select(KnowledgeBase).where(KnowledgeBase.kb_id == kb_id))
        if kb is not None:
            await session.delete(kb)
        user = await session.get(User, user_id)
        if user is not None:
            await session.delete(user)
        department = await session.get(Department, department_id)
        if department is not None:
            await session.delete(department)


async def test_risk_lifecycle_persists_audit_and_enforces_transitions(monkeypatch: pytest.MonkeyPatch):
    current_user, project, department_id, user_id = await _seed_risk_context()
    try:
        async def owned_project(project_id, current_user):
            assert project_id == project.project_id
            return project

        monkeypatch.setattr(research_project_risk_service, "get_owned_project", owned_project)
        created = await research_project_risk_service.create_project_risk(
            project_id=project.project_id,
            current_user=current_user,
            title="数据源失效",
            description="外部数据源可能下线",
            severity="high",
            owner_uid=current_user.uid,
            mitigation="准备镜像数据源",
            due_date=None,
        )
        assert created["status"] == "open"

        transitioned = await research_project_risk_service.transition_project_risk(
            project_id=project.project_id,
            risk_id=created["risk_id"],
            current_user=current_user,
            status="mitigating",
            reason="已启动替代源验证",
        )
        assert transitioned["status"] == "mitigating"

        await research_project_risk_service.transition_project_risk(
            project_id=project.project_id,
            risk_id=created["risk_id"],
            current_user=current_user,
            status="closed",
        )

        with pytest.raises(RuntimeError, match="不能从 closed 转换为 open"):
            await research_project_risk_service.transition_project_risk(
                project_id=project.project_id,
                risk_id=created["risk_id"],
                current_user=current_user,
                status="open",
            )

        activities = await research_project_risk_service.list_project_risk_activities(
            project_id=project.project_id,
            risk_id=created["risk_id"],
            current_user=current_user,
        )
        assert [item["activity_type"] for item in activities] == ["created", "transitioned", "transitioned"]
        assert activities[1]["precondition"] == {"previous_status": "open"}
        assert activities[1]["changes"]["reason"] == "已启动替代源验证"

        async with pg_manager.get_async_session_context() as session:
            risk = await session.scalar(select(ResearchProjectRisk).where(ResearchProjectRisk.risk_id == created["risk_id"]))
            audit_count = await session.scalar(
                select(ResearchProjectRiskActivity.id).where(ResearchProjectRiskActivity.risk_id == created["risk_id"])
            )
            assert risk is not None and risk.status == "closed"
            assert audit_count is not None
    finally:
        await _cleanup(project.kb_id, department_id, user_id)


async def test_risk_creation_rejects_unknown_owner(monkeypatch: pytest.MonkeyPatch):
    current_user, project, department_id, user_id = await _seed_risk_context()
    try:
        async def owned_project(project_id, current_user):
            return project

        monkeypatch.setattr(research_project_risk_service, "get_owned_project", owned_project)
        with pytest.raises(ValueError, match="责任人不存在"):
            await research_project_risk_service.create_project_risk(
                project_id=project.project_id,
                current_user=current_user,
                title="未知责任人",
                description="应拒绝",
                severity="medium",
                owner_uid="missing-owner",
                mitigation="",
                due_date=None,
            )
    finally:
        await _cleanup(project.kb_id, department_id, user_id)
