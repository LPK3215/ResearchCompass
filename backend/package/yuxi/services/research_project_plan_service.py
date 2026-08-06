"""ResearchCompass 研究项目执行计划服务。

本模块是本仓库作者在开源智能体框架 Yuxi 之上实现的研究项目执行计划治理业务，
负责里程碑、任务、以及"项目成果 → 执行项"证据关联的增删改与排序。项目可见性、
资产可用性校验等通用持久化能力由 Yuxi 仓库层提供；本模块定义执行计划的状态机、
完成约束与成果关联语义，供科研用户在项目工作区内推进研究进度。
"""

from __future__ import annotations

from collections.abc import Awaitable
from datetime import date
from typing import Any

from sqlalchemy.exc import IntegrityError

from yuxi.repositories.research_project_plan_repository import (
    ResearchProjectPlanRepository,
    ResearchProjectPlanWriteConflict,
)
from yuxi.repositories.research_project_repository import ResearchProjectRepository
from yuxi.services.research_project_plan_utils import (
    MILESTONE_STATUSES,
    TASK_PRIORITIES,
    TASK_STATUSES,
    build_project_plan_summary,
)
from yuxi.services.research_project_service import (
    ResearchProjectError,
    ensure_project_writable,
    get_available_project_asset_ids,
    get_owned_project,
    serialize_project_asset,
)
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_knowledge import (
    ResearchProject,
    ResearchProjectAsset,
    ResearchProjectMilestone,
    ResearchProjectPlanAssetLink,
    ResearchProjectTask,
    ResearchProjectTaskDependency,
)
from yuxi.utils.datetime_utils import utc_now_naive


async def _execute_plan_write[T](operation: Awaitable[T]) -> T:
    try:
        return await operation
    except ResearchProjectPlanWriteConflict as exc:
        raise ResearchProjectError("project_read_only", "已完成或归档项目需恢复为进行中后才能修改成果") from exc


def serialize_milestone(
    milestone: ResearchProjectMilestone,
    tasks: list[ResearchProjectTask],
    links: list[dict[str, Any]],
) -> dict[str, Any]:
    milestone_tasks = [task for task in tasks if task.milestone_id == milestone.milestone_id]
    completed_tasks = sum(task.status == "done" for task in milestone_tasks)
    if milestone_tasks:
        progress = round(completed_tasks * 100 / len(milestone_tasks))
    else:
        progress = 100 if milestone.status == "completed" else 0
    return {
        "milestone_id": milestone.milestone_id,
        "title": milestone.title,
        "description": milestone.description or "",
        "status": milestone.status,
        "target_date": milestone.target_date.isoformat() if milestone.target_date else None,
        "sort_order": milestone.sort_order,
        "progress": progress,
        "task_counts": {
            "total": len(milestone_tasks),
            "completed": completed_tasks,
            "open": len(milestone_tasks) - completed_tasks,
        },
        "asset_links": [link for link in links if link["milestone_id"] == milestone.milestone_id],
        "created_at": milestone.created_at.isoformat() if milestone.created_at else None,
        "updated_at": milestone.updated_at.isoformat() if milestone.updated_at else None,
        "completed_at": milestone.completed_at.isoformat() if milestone.completed_at else None,
    }


def serialize_task(task: ResearchProjectTask, links: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "task_id": task.task_id,
        "milestone_id": task.milestone_id,
        "title": task.title,
        "description": task.description or "",
        "status": task.status,
        "priority": task.priority,
        "due_date": task.due_date.isoformat() if task.due_date else None,
        "sort_order": task.sort_order,
        "asset_links": [link for link in links if link["task_id"] == task.task_id],
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
    }


def serialize_task_dependency(dependency: ResearchProjectTaskDependency) -> dict[str, Any]:
    return {
        "dependency_id": dependency.dependency_id,
        "project_id": dependency.project_id,
        "task_id": dependency.task_id,
        "depends_on_task_id": dependency.depends_on_task_id,
        "created_by": dependency.created_by,
        "created_at": dependency.created_at.isoformat() if dependency.created_at else None,
    }


def serialize_asset_link(
    link: ResearchProjectPlanAssetLink,
    asset: ResearchProjectAsset,
    *,
    available: bool,
) -> dict[str, Any]:
    return {
        "link_id": link.link_id,
        "asset_id": link.asset_id,
        "milestone_id": link.milestone_id,
        "task_id": link.task_id,
        "target_type": "milestone" if link.milestone_id else "task",
        "target_id": link.milestone_id or link.task_id,
        "asset": serialize_project_asset(asset, available=available),
        "created_at": link.created_at.isoformat() if link.created_at else None,
    }


async def get_research_project_plan(
    *,
    project_id: str,
    current_user: User,
    project: ResearchProject | None = None,
    available_asset_ids: set[str] | None = None,
) -> dict[str, Any]:
    project = project or await get_owned_project(project_id, current_user)
    repository = ResearchProjectPlanRepository()
    milestones, tasks, link_records = await repository.get_plan_records(project_id)
    dependencies = await repository.list_task_dependencies(project_id)
    assets = list({asset.asset_id: asset for _, asset in link_records}.values())
    if available_asset_ids is None:
        available_asset_ids = await get_available_project_asset_ids(project, current_user, assets)
    links = [
        serialize_asset_link(link, asset, available=asset.asset_id in available_asset_ids)
        for link, asset in link_records
    ]
    serialized_tasks = [serialize_task(task, links) for task in tasks]
    task_groups: dict[str | None, list[dict[str, Any]]] = {}
    for task in serialized_tasks:
        task_groups.setdefault(task["milestone_id"], []).append(task)
    serialized_milestones = []
    for milestone in milestones:
        item = serialize_milestone(milestone, tasks, links)
        item["tasks"] = task_groups.get(milestone.milestone_id, [])
        serialized_milestones.append(item)
    return {
        "project_id": project.project_id,
        "read_only": project.status != "active",
        "summary": build_project_plan_summary(
            project_status=project.status,
            manual_progress=int(project.progress or 0),
            milestones=milestones,
            tasks=tasks,
        ),
        "milestones": serialized_milestones,
        "unassigned_tasks": task_groups.get(None, []),
        "asset_links": links,
        "task_dependencies": [serialize_task_dependency(item) for item in dependencies],
    }


async def create_project_milestone(
    *,
    project_id: str,
    current_user: User,
    title: str,
    description: str,
    status: str,
    target_date: date | None,
) -> dict[str, Any]:
    project = await get_owned_project(project_id, current_user)
    ensure_project_writable(project)
    _validate_milestone_status(status)
    record = await _execute_plan_write(
        ResearchProjectPlanRepository().create_milestone(
            project_id,
            {
                "title": title,
                "description": description or None,
                "status": status,
                "target_date": target_date,
                "completed_at": utc_now_naive() if status == "completed" else None,
            }, operator_uid=str(current_user.uid),
        )
    )
    return serialize_milestone(record, [], [])


async def update_project_milestone(
    *,
    project_id: str,
    milestone_id: str,
    current_user: User,
    values: dict[str, Any],
) -> dict[str, Any]:
    project = await get_owned_project(project_id, current_user)
    ensure_project_writable(project)
    repository = ResearchProjectPlanRepository()
    current = await repository.get_milestone(project_id, milestone_id)
    if current is None:
        raise ResearchProjectError("milestone_not_found", "里程碑不存在")
    status = values.get("status", current.status)
    _validate_milestone_status(status)
    if "description" in values:
        values["description"] = values["description"] or None
    if status != current.status:
        values["completed_at"] = utc_now_naive() if status == "completed" else None
    record, open_tasks = await _execute_plan_write(
        repository.update_milestone(
            project_id,
            milestone_id,
            values=values,
            activity_type="milestone_status_changed" if status != current.status else "milestone_updated",
            require_no_open_tasks=status == "completed",
            operator_uid=str(current_user.uid),
        )
    )
    if open_tasks:
        raise ResearchProjectError("milestone_has_open_tasks", "里程碑仍有未完成任务，不能标记完成")
    if record is None:
        raise ResearchProjectError("milestone_not_found", "里程碑不存在")
    _, tasks, link_records = await repository.get_plan_records(project_id)
    links = [serialize_asset_link(link, asset, available=True) for link, asset in link_records]
    return serialize_milestone(record, tasks, links)


async def delete_project_milestone(*, project_id: str, milestone_id: str, current_user: User) -> None:
    project = await get_owned_project(project_id, current_user)
    ensure_project_writable(project)
    record, task_count = await _execute_plan_write(
        ResearchProjectPlanRepository().delete_milestone(project_id, milestone_id, operator_uid=str(current_user.uid))
    )
    if record is None:
        raise ResearchProjectError("milestone_not_found", "里程碑不存在")
    if task_count:
        raise ResearchProjectError("milestone_has_tasks", "里程碑包含任务，请先移动或删除这些任务")


async def reorder_project_milestones(*, project_id: str, current_user: User, milestone_ids: list[str]) -> None:
    project = await get_owned_project(project_id, current_user)
    ensure_project_writable(project)
    if not await _execute_plan_write(ResearchProjectPlanRepository().reorder_milestones(project_id, milestone_ids, operator_uid=str(current_user.uid))):
        raise ResearchProjectError("invalid_milestone_order", "排序必须包含当前项目的全部里程碑且不能重复")


async def create_project_task(
    *,
    project_id: str,
    current_user: User,
    title: str,
    description: str,
    status: str,
    priority: str,
    due_date: date | None,
    milestone_id: str | None,
) -> dict[str, Any]:
    project = await get_owned_project(project_id, current_user)
    ensure_project_writable(project)
    _validate_task_values(status, priority)
    repository = ResearchProjectPlanRepository()
    if milestone_id and await repository.get_milestone(project_id, milestone_id) is None:
        raise ResearchProjectError("milestone_not_found", "里程碑不存在")
    record = await _execute_plan_write(
        repository.create_task(
            project_id,
            {
                "title": title,
                "description": description or None,
                "status": status,
                "priority": priority,
                "due_date": due_date,
                "milestone_id": milestone_id,
                "completed_at": utc_now_naive() if status == "done" else None,
            }, operator_uid=str(current_user.uid),
        )
    )
    if record is None:
        raise ResearchProjectError("milestone_not_found", "里程碑不存在")
    return serialize_task(record, [])


async def update_project_task(
    *,
    project_id: str,
    task_id: str,
    current_user: User,
    values: dict[str, Any],
) -> dict[str, Any]:
    project = await get_owned_project(project_id, current_user)
    ensure_project_writable(project)
    repository = ResearchProjectPlanRepository()
    current = await repository.get_task(project_id, task_id)
    if current is None:
        raise ResearchProjectError("task_not_found", "任务不存在")
    status = values.get("status", current.status)
    priority = values.get("priority", current.priority)
    _validate_task_values(status, priority)
    if "milestone_id" in values and values["milestone_id"]:
        if await repository.get_milestone(project_id, values["milestone_id"]) is None:
            raise ResearchProjectError("milestone_not_found", "里程碑不存在")
    if "description" in values:
        values["description"] = values["description"] or None
    if status != current.status:
        values["completed_at"] = utc_now_naive() if status == "done" else None
    record = await _execute_plan_write(
        repository.update_task(
            project_id,
            task_id,
            values=values,
            activity_type="task_status_changed" if status != current.status else "task_updated",
            operator_uid=str(current_user.uid),
        )
    )
    if record is None:
        raise ResearchProjectError("task_not_found", "任务不存在")
    _, _, link_records = await repository.get_plan_records(project_id)
    links = [serialize_asset_link(link, asset, available=True) for link, asset in link_records]
    return serialize_task(record, links)


async def delete_project_task(*, project_id: str, task_id: str, current_user: User) -> None:
    project = await get_owned_project(project_id, current_user)
    ensure_project_writable(project)
    if await _execute_plan_write(ResearchProjectPlanRepository().delete_task(project_id, task_id, operator_uid=str(current_user.uid))) is None:
        raise ResearchProjectError("task_not_found", "任务不存在")


async def create_project_task_dependency(
    *, project_id: str, task_id: str, depends_on_task_id: str, current_user: User
) -> dict[str, Any]:
    project = await get_owned_project(project_id, current_user)
    ensure_project_writable(project)
    if task_id == depends_on_task_id:
        raise ResearchProjectError("task_dependency_self", "任务不能依赖自身")
    repository = ResearchProjectPlanRepository()
    dependencies = await repository.list_task_dependencies(project_id)
    graph: dict[str, set[str]] = {}
    for item in dependencies:
        graph.setdefault(str(item.task_id), set()).add(str(item.depends_on_task_id))
    graph.setdefault(task_id, set()).add(depends_on_task_id)
    pending = [depends_on_task_id]
    visited: set[str] = set()
    while pending:
        node = pending.pop()
        if node == task_id:
            raise ResearchProjectError("task_dependency_cycle", "任务依赖不能形成循环")
        if node in visited:
            continue
        visited.add(node)
        pending.extend(graph.get(node, set()))
    try:
        record = await _execute_plan_write(
            repository.create_task_dependency(
                project_id, task_id, depends_on_task_id, created_by=str(current_user.uid)
            )
        )
    except IntegrityError as exc:
        raise ResearchProjectError("task_dependency_exists", "该任务依赖关系已存在") from exc
    if record is None:
        raise ResearchProjectError("task_not_found", "任务不存在或不属于当前项目")
    return serialize_task_dependency(record)


async def delete_project_task_dependency(*, project_id: str, dependency_id: str, current_user: User) -> None:
    project = await get_owned_project(project_id, current_user)
    ensure_project_writable(project)
    record = await _execute_plan_write(
        ResearchProjectPlanRepository().delete_task_dependency(
            project_id, dependency_id, operator_uid=str(current_user.uid)
        )
    )
    if record is None:
        raise ResearchProjectError("task_dependency_not_found", "任务依赖关系不存在")


async def reorder_project_tasks(
    *,
    project_id: str,
    current_user: User,
    milestone_id: str | None,
    task_ids: list[str],
) -> None:
    project = await get_owned_project(project_id, current_user)
    ensure_project_writable(project)
    repository = ResearchProjectPlanRepository()
    if milestone_id and await repository.get_milestone(project_id, milestone_id) is None:
        raise ResearchProjectError("milestone_not_found", "里程碑不存在")
    if not await _execute_plan_write(repository.reorder_tasks(project_id, milestone_id, task_ids, operator_uid=str(current_user.uid))):
        raise ResearchProjectError("invalid_task_order", "排序必须包含当前分组的全部任务且不能重复")


async def create_project_plan_asset_link(
    *,
    project_id: str,
    current_user: User,
    asset_id: str,
    milestone_id: str | None,
    task_id: str | None,
) -> dict[str, Any]:
    project = await get_owned_project(project_id, current_user)
    ensure_project_writable(project)
    if bool(milestone_id) == bool(task_id):
        raise ResearchProjectError("invalid_plan_asset_target", "成果必须且只能关联一个里程碑或任务")
    repository = ResearchProjectPlanRepository()
    asset = await ResearchProjectRepository().get_asset(project_id, asset_id)
    if asset is None:
        raise ResearchProjectError("asset_not_found", "项目成果不存在")
    if asset.asset_type == "evidence":
        available = await get_available_project_asset_ids(project, current_user, [asset])
        if asset.asset_id not in available:
            raise ResearchProjectError("evidence_unavailable", "失效或归档证据不能关联执行项")
    if await repository.asset_link_exists(
        project_id,
        asset_id=asset_id,
        milestone_id=milestone_id,
        task_id=task_id,
    ):
        raise ResearchProjectError("plan_asset_already_linked", "该成果已经关联到这个执行项")
    try:
        link = await _execute_plan_write(
            repository.create_asset_link(
                project_id,
                asset_id=asset_id,
                milestone_id=milestone_id,
                task_id=task_id,
                operator_uid=str(current_user.uid),
            )
        )
    except IntegrityError as exc:
        raise ResearchProjectError("plan_asset_already_linked", "该成果已经关联到这个执行项") from exc
    if link is None:
        raise ResearchProjectError("plan_asset_target_not_found", "项目成果或执行项不存在")
    available = await get_available_project_asset_ids(project, current_user, [asset])
    return serialize_asset_link(link, asset, available=asset.asset_id in available)


async def delete_project_plan_asset_link(*, project_id: str, link_id: str, current_user: User) -> None:
    project = await get_owned_project(project_id, current_user)
    ensure_project_writable(project)
    if await _execute_plan_write(ResearchProjectPlanRepository().delete_asset_link(project_id, link_id, operator_uid=str(current_user.uid))) is None:
        raise ResearchProjectError("plan_asset_link_not_found", "成果关联不存在")


def _validate_milestone_status(status: str) -> None:
    if status not in MILESTONE_STATUSES:
        raise ResearchProjectError("invalid_milestone_status", "不支持的里程碑状态")


def _validate_task_values(status: str, priority: str) -> None:
    if status not in TASK_STATUSES:
        raise ResearchProjectError("invalid_task_status", "不支持的任务状态")
    if priority not in TASK_PRIORITIES:
        raise ResearchProjectError("invalid_task_priority", "不支持的任务优先级")


__all__ = [
    "create_project_milestone",
    "create_project_plan_asset_link",
    "create_project_task",
    "create_project_task_dependency",
    "delete_project_milestone",
    "delete_project_plan_asset_link",
    "delete_project_task",
    "delete_project_task_dependency",
    "get_research_project_plan",
    "reorder_project_milestones",
    "reorder_project_tasks",
    "serialize_asset_link",
    "serialize_milestone",
    "serialize_task",
    "serialize_task_dependency",
    "update_project_milestone",
    "update_project_task",
]
