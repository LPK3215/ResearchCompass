from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import func, select

from yuxi.repositories.research_project_plan_repository import ResearchProjectPlanRepository
from yuxi.repositories.research_project_repository import ResearchProjectRepository
from yuxi.services import research_project_plan_service, research_project_service
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_knowledge import (
    KnowledgeBase,
    ResearchProject,
    ResearchProjectMilestone,
    ResearchProjectTask,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _seed_project() -> tuple[str, str, str]:
    pg_manager.initialize()
    await pg_manager.create_tables()
    suffix = uuid.uuid4().hex
    uid = f"concurrency-user-{suffix}"
    kb_id = f"concurrency-kb-{suffix}"
    project_id = f"concurrency-project-{suffix}"
    async with pg_manager.get_async_session_context() as session:
        session.add(
            KnowledgeBase(
                kb_id=kb_id,
                name=f"Concurrency test {suffix}",
                kb_type="milvus",
                created_by=uid,
            )
        )
        session.add(
            ResearchProject(
                project_id=project_id,
                kb_id=kb_id,
                uid=uid,
                title="Concurrency test",
                research_question="Can completion race with an open task?",
                status="active",
                progress=0,
                tags=[],
            )
        )
    return uid, kb_id, project_id


async def _delete_knowledge_base(kb_id: str) -> None:
    async with pg_manager.get_async_session_context() as session:
        record = await session.scalar(select(KnowledgeBase).where(KnowledgeBase.kb_id == kb_id))
        if record is not None:
            await session.delete(record)


def _patch_project_access(monkeypatch: pytest.MonkeyPatch, uid: str) -> None:
    async def get_owned_project(project_id: str, current_user):
        assert str(current_user.uid) == uid
        project = await ResearchProjectRepository().get(project_id, uid=uid)
        assert project is not None
        return project

    monkeypatch.setattr(research_project_service, "get_owned_project", get_owned_project)
    monkeypatch.setattr(research_project_plan_service, "get_owned_project", get_owned_project)


def _pause_project_update(monkeypatch: pytest.MonkeyPatch) -> tuple[asyncio.Event, asyncio.Event]:
    reached_update = asyncio.Event()
    resume_update = asyncio.Event()
    original = ResearchProjectRepository.update

    async def update(
        self,
        project_id: str,
        *,
        uid: str,
        values: dict[str, Any],
        activity_type: str,
        require_no_open_tasks: bool = False,
    ):
        reached_update.set()
        await resume_update.wait()
        return await original(
            self,
            project_id,
            uid=uid,
            values=values,
            activity_type=activity_type,
            require_no_open_tasks=require_no_open_tasks,
        )

    monkeypatch.setattr(ResearchProjectRepository, "update", update)
    return reached_update, resume_update


def _pause_milestone_update(monkeypatch: pytest.MonkeyPatch) -> tuple[asyncio.Event, asyncio.Event]:
    reached_update = asyncio.Event()
    resume_update = asyncio.Event()
    original = ResearchProjectPlanRepository.update_milestone

    async def update_milestone(
        self,
        project_id: str,
        milestone_id: str,
        *,
        values: dict[str, Any],
        activity_type: str,
        require_no_open_tasks: bool = False,
        operator_uid: str,
    ):
        reached_update.set()
        await resume_update.wait()
        return await original(
            self,
            project_id,
            milestone_id,
            values=values,
            activity_type=activity_type,
            require_no_open_tasks=require_no_open_tasks,
            operator_uid=operator_uid,
        )

    monkeypatch.setattr(ResearchProjectPlanRepository, "update_milestone", update_milestone)
    return reached_update, resume_update


async def test_project_completion_rechecks_tasks_in_locked_transaction(monkeypatch: pytest.MonkeyPatch):
    uid, kb_id, project_id = await _seed_project()
    current_user = SimpleNamespace(uid=uid)
    _patch_project_access(monkeypatch, uid)
    reached_update, resume_update = _pause_project_update(monkeypatch)
    completion = None

    try:
        completion = asyncio.create_task(
            research_project_service.update_research_project(
                project_id=project_id,
                current_user=current_user,
                values={"status": "completed"},
            )
        )
        await asyncio.wait_for(reached_update.wait(), timeout=5)
        await research_project_plan_service.create_project_task(
            project_id=project_id,
            current_user=current_user,
            title="Task committed before project completion",
            description="",
            status="todo",
            priority="medium",
            due_date=None,
            milestone_id=None,
        )
        resume_update.set()
        with pytest.raises(research_project_service.ResearchProjectError) as exc_info:
            await asyncio.wait_for(completion, timeout=5)

        assert exc_info.value.error_type == "project_has_open_tasks"
        async with pg_manager.get_async_session_context() as session:
            stored_status = await session.scalar(
                select(ResearchProject.status).where(ResearchProject.project_id == project_id)
            )
            open_tasks = await session.scalar(
                select(func.count())
                .select_from(ResearchProjectTask)
                .where(
                    ResearchProjectTask.project_id == project_id,
                    ResearchProjectTask.status != "done",
                )
            )
        assert stored_status == "active"
        assert int(open_tasks or 0) == 1
    finally:
        resume_update.set()
        if completion is not None:
            await asyncio.gather(completion, return_exceptions=True)
        await _delete_knowledge_base(kb_id)


async def test_milestone_completion_rechecks_tasks_while_holding_milestone_lock(
    monkeypatch: pytest.MonkeyPatch,
):
    uid, kb_id, project_id = await _seed_project()
    current_user = SimpleNamespace(uid=uid)
    _patch_project_access(monkeypatch, uid)
    completion = None

    try:
        milestone = await research_project_plan_service.create_project_milestone(
            project_id=project_id,
            current_user=current_user,
            title="Concurrency milestone",
            description="",
            status="active",
            target_date=None,
        )
        milestone_id = milestone["milestone_id"]
        reached_update, resume_update = _pause_milestone_update(monkeypatch)
        completion = asyncio.create_task(
            research_project_plan_service.update_project_milestone(
                project_id=project_id,
                milestone_id=milestone_id,
                current_user=current_user,
                values={"status": "completed"},
            )
        )
        await asyncio.wait_for(reached_update.wait(), timeout=5)
        await research_project_plan_service.create_project_task(
            project_id=project_id,
            current_user=current_user,
            title="Task committed before milestone completion",
            description="",
            status="todo",
            priority="medium",
            due_date=None,
            milestone_id=milestone_id,
        )
        resume_update.set()
        with pytest.raises(research_project_service.ResearchProjectError) as exc_info:
            await asyncio.wait_for(completion, timeout=5)

        assert exc_info.value.error_type == "milestone_has_open_tasks"
        async with pg_manager.get_async_session_context() as session:
            stored_status = await session.scalar(
                select(ResearchProjectMilestone.status).where(ResearchProjectMilestone.milestone_id == milestone_id)
            )
            open_tasks = await session.scalar(
                select(func.count())
                .select_from(ResearchProjectTask)
                .where(
                    ResearchProjectTask.project_id == project_id,
                    ResearchProjectTask.milestone_id == milestone_id,
                    ResearchProjectTask.status != "done",
                )
            )
        assert stored_status == "active"
        assert int(open_tasks or 0) == 1
    finally:
        if "resume_update" in locals():
            resume_update.set()
        if completion is not None:
            await asyncio.gather(completion, return_exceptions=True)
        await _delete_knowledge_base(kb_id)


async def test_task_transaction_rolls_back_if_project_completes_before_commit(monkeypatch: pytest.MonkeyPatch):
    uid, kb_id, project_id = await _seed_project()
    current_user = SimpleNamespace(uid=uid)
    _patch_project_access(monkeypatch, uid)
    task_reached_project_touch = asyncio.Event()
    resume_task = asyncio.Event()
    original_touch_project = ResearchProjectPlanRepository._touch_project
    creation = None

    async def pause_before_project_touch(session, touched_project_id: str, *, sync_progress: bool = False) -> None:
        task_reached_project_touch.set()
        await resume_task.wait()
        await original_touch_project(session, touched_project_id, sync_progress=sync_progress)

    monkeypatch.setattr(
        ResearchProjectPlanRepository,
        "_touch_project",
        staticmethod(pause_before_project_touch),
    )

    try:
        creation = asyncio.create_task(
            research_project_plan_service.create_project_task(
                project_id=project_id,
                current_user=current_user,
                title="Uncommitted task",
                description="",
                status="todo",
                priority="medium",
                due_date=None,
                milestone_id=None,
            )
        )
        await asyncio.wait_for(task_reached_project_touch.wait(), timeout=5)
        await research_project_service.update_research_project(
            project_id=project_id,
            current_user=current_user,
            values={"status": "completed"},
        )
        resume_task.set()
        with pytest.raises(research_project_service.ResearchProjectError) as exc_info:
            await asyncio.wait_for(creation, timeout=5)

        assert exc_info.value.error_type == "project_read_only"
        async with pg_manager.get_async_session_context() as session:
            stored_status = await session.scalar(
                select(ResearchProject.status).where(ResearchProject.project_id == project_id)
            )
            task_count = await session.scalar(
                select(func.count())
                .select_from(ResearchProjectTask)
                .where(ResearchProjectTask.project_id == project_id)
            )
        assert stored_status == "completed"
        assert int(task_count or 0) == 0
    finally:
        resume_task.set()
        if creation is not None:
            await asyncio.gather(creation, return_exceptions=True)
        await _delete_knowledge_base(kb_id)
