from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, date, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from yuxi.repositories.research_project_repository import ASSET_TYPES, ResearchProjectRepository
from yuxi.repositories.research_project_plan_repository import ResearchProjectPlanRepository
from yuxi.services.research_project_plan_utils import build_project_plan_summary
from yuxi.services.research_paper_service import _ensure_access
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_knowledge import ResearchProject, ResearchProjectActivity, ResearchProjectAsset


PROJECT_STATUSES = {"active", "completed", "archived"}


class ResearchProjectError(RuntimeError):
    def __init__(self, error_type: str, message: str):
        super().__init__(message)
        self.error_type = error_type
        self.message = message


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def get_owned_project(project_id: str, current_user: User) -> ResearchProject:
    project = await ResearchProjectRepository().get(project_id, uid=str(current_user.uid))
    if project is None:
        raise ResearchProjectError("project_not_found", "研究项目不存在")
    try:
        await _ensure_access(current_user, str(project.kb_id))
    except HTTPException as exc:
        if exc.status_code != 403:
            raise
        raise ResearchProjectError("forbidden", "无权访问该研究项目所属知识库") from exc
    return project


def serialize_project(
    project: ResearchProject,
    counts: dict[str, int],
    plan_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "project_id": project.project_id,
        "kb_id": project.kb_id,
        "title": project.title,
        "research_question": project.research_question,
        "description": project.description or "",
        "status": project.status,
        "progress": int(project.progress or 0),
        "next_action": project.next_action or "",
        "tags": project.tags or [],
        "target_date": project.target_date.isoformat() if project.target_date else None,
        "asset_counts": counts,
        "created_at": project.created_at.isoformat() if project.created_at else None,
        "updated_at": project.updated_at.isoformat() if project.updated_at else None,
        "completed_at": project.completed_at.isoformat() if project.completed_at else None,
        "archived_at": project.archived_at.isoformat() if project.archived_at else None,
    }
    if plan_summary is not None:
        payload.update(
            {
                "progress": plan_summary["progress"],
                "progress_source": plan_summary["progress_source"],
                "health": plan_summary["health"],
                "next_due_date": plan_summary["next_due_date"],
                "plan_summary": {
                    "milestones": plan_summary["milestones"],
                    "tasks": plan_summary["tasks"],
                },
            }
        )
    return payload


def serialize_project_asset(asset: ResearchProjectAsset, *, available: bool) -> dict[str, Any]:
    return {
        "asset_id": asset.asset_id,
        "asset_type": asset.asset_type,
        "reference_id": asset.reference_id,
        "title": asset.title_snapshot,
        "summary": asset.summary_snapshot or "",
        "status": asset.status_snapshot,
        "metadata": asset.metadata_snapshot or {},
        "notes": asset.notes or "",
        "available": available,
        "added_at": asset.added_at.isoformat() if asset.added_at else None,
        "updated_at": asset.updated_at.isoformat() if asset.updated_at else None,
    }


def serialize_project_activity(activity: ResearchProjectActivity) -> dict[str, Any]:
    return {
        "activity_id": activity.activity_id,
        "activity_type": activity.activity_type,
        "asset_type": activity.asset_type,
        "reference_id": activity.reference_id,
        "payload": activity.payload or {},
        "created_at": activity.created_at.isoformat() if activity.created_at else None,
    }


async def create_research_project(
    *,
    kb_id: str,
    current_user: User,
    title: str,
    research_question: str,
    description: str,
    tags: list[str],
    target_date: date | None,
    next_action: str,
) -> dict[str, Any]:
    await _ensure_access(current_user, kb_id)
    project = await ResearchProjectRepository().create(
        {
            "project_id": uuid.uuid4().hex,
            "kb_id": kb_id,
            "uid": str(current_user.uid),
            "title": title,
            "research_question": research_question,
            "description": description or None,
            "status": "active",
            "progress": 0,
            "next_action": next_action or None,
            "tags": tags,
            "target_date": target_date,
        }
    )
    summary = build_project_plan_summary(
        project_status=project.status,
        manual_progress=int(project.progress or 0),
        milestones=[],
        tasks=[],
    )
    return serialize_project(project, {**{item: 0 for item in ASSET_TYPES}, "total": 0}, summary)


async def list_research_projects(
    *,
    kb_id: str,
    current_user: User,
    status: str | None,
    query: str | None,
    offset: int,
    limit: int,
) -> dict[str, Any]:
    await _ensure_access(current_user, kb_id)
    repository = ResearchProjectRepository()
    projects, total, counts = await repository.list_for_user(
        uid=str(current_user.uid),
        kb_id=kb_id,
        status=status,
        query=query,
        offset=offset,
        limit=limit,
    )
    milestone_records, task_records = await ResearchProjectPlanRepository().list_summary_records(
        [str(project.project_id) for project in projects]
    )
    return {
        "items": [
            serialize_project(
                project,
                counts[project.project_id],
                build_project_plan_summary(
                    project_status=project.status,
                    manual_progress=int(project.progress or 0),
                    milestones=milestone_records[project.project_id],
                    tasks=task_records[project.project_id],
                ),
            )
            for project in projects
        ],
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(projects) < total,
    }


async def get_research_project(*, project_id: str, current_user: User) -> dict[str, Any]:
    project = await get_owned_project(project_id, current_user)
    repository = ResearchProjectRepository()
    counts = await repository.get_asset_counts(project_id)
    activities = await repository.list_activities(project_id, limit=30)
    milestones, tasks, _ = await ResearchProjectPlanRepository().get_plan_records(project_id)
    summary = build_project_plan_summary(
        project_status=project.status,
        manual_progress=int(project.progress or 0),
        milestones=milestones,
        tasks=tasks,
    )
    payload = serialize_project(project, counts, summary)
    payload["activities"] = [serialize_project_activity(item) for item in activities]
    return payload


async def update_research_project(
    *,
    project_id: str,
    current_user: User,
    values: dict[str, Any],
) -> dict[str, Any]:
    project = await get_owned_project(project_id, current_user)
    if project.status == "archived" and values.get("status") != "active":
        raise ResearchProjectError("project_archived", "归档项目需先恢复为进行中才能编辑")
    if project.status == "completed" and values.get("status", "completed") == "completed":
        disallowed = set(values) - {"description", "progress"}
        if disallowed:
            raise ResearchProjectError("project_read_only", "已完成项目只能修订项目说明、归档或恢复为进行中")
        values.pop("progress", None)

    status = values.get("status", project.status)
    if status not in PROJECT_STATUSES:
        raise ResearchProjectError("invalid_project_status", "不支持的研究项目状态")
    activity_type = "project_status_changed" if status != project.status else "project_updated"
    plan_repository = ResearchProjectPlanRepository()
    if status == "completed":
        open_tasks = await plan_repository.count_open_tasks(project_id)
        if open_tasks:
            raise ResearchProjectError(
                "project_has_open_tasks",
                f"项目仍有 {open_tasks} 个未完成任务，完成全部任务后才能标记项目完成",
            )
    milestones, tasks, _ = await plan_repository.get_plan_records(project_id)
    if tasks:
        values.pop("progress", None)
    if status == "completed" and "progress" in values:
        values["progress"] = 100
    if status != project.status:
        if status == "completed":
            values["progress"] = 100
            values["completed_at"] = _now()
            values["archived_at"] = None
        elif status == "archived":
            values["archived_at"] = _now()
        else:
            values["completed_at"] = None
            values["archived_at"] = None
    updated = await ResearchProjectRepository().update(
        project_id,
        uid=str(current_user.uid),
        values=values,
        activity_type=activity_type,
    )
    if updated is None:
        raise ResearchProjectError("project_not_found", "研究项目不存在")
    summary = build_project_plan_summary(
        project_status=updated.status,
        manual_progress=int(updated.progress or 0),
        milestones=milestones,
        tasks=tasks,
    )
    return serialize_project(updated, await ResearchProjectRepository().get_asset_counts(project_id), summary)


async def delete_research_project(*, project_id: str, current_user: User) -> None:
    await get_owned_project(project_id, current_user)
    if not await ResearchProjectRepository().delete(project_id, uid=str(current_user.uid)):
        raise ResearchProjectError("project_not_found", "研究项目不存在")


async def list_project_asset_candidates(
    *,
    project_id: str,
    current_user: User,
    asset_type: str,
    query: str | None,
    offset: int,
    limit: int,
) -> dict[str, Any]:
    project = await get_owned_project(project_id, current_user)
    _validate_asset_type(asset_type, current_user)
    items, total = await ResearchProjectRepository().list_candidates(
        project_id=project_id,
        kb_id=str(project.kb_id),
        uid=str(current_user.uid),
        asset_type=asset_type,
        query=query,
        offset=offset,
        limit=limit,
    )
    return {"items": items, "total": total, "offset": offset, "limit": limit, "has_more": offset + len(items) < total}


async def add_project_assets(
    *,
    project_id: str,
    current_user: User,
    asset_type: str,
    reference_ids: list[str],
    notes: str,
) -> dict[str, Any]:
    project = await get_owned_project(project_id, current_user)
    ensure_project_writable(project)
    _validate_asset_type(asset_type, current_user)
    repository = ResearchProjectRepository()
    linked = await repository.get_linked_reference_ids(project_id, asset_type)
    duplicates = [reference_id for reference_id in reference_ids if reference_id in linked]
    if duplicates:
        raise ResearchProjectError("asset_already_linked", "所选成果已存在于当前研究项目")

    candidates = await repository.get_candidates(
        kb_id=str(project.kb_id),
        uid=str(current_user.uid),
        asset_type=asset_type,
        reference_ids=reference_ids,
    )
    missing = [reference_id for reference_id in reference_ids if reference_id not in candidates]
    if missing:
        raise ResearchProjectError("asset_not_found", "研究成果不存在、无权访问或不属于当前知识库")

    snapshots = []
    for reference_id in reference_ids:
        candidate = candidates[reference_id]
        snapshots.append(
            {
                "asset_type": asset_type,
                "reference_id": reference_id,
                "title_snapshot": candidate["title"],
                "summary_snapshot": candidate.get("summary") or None,
                "status_snapshot": candidate.get("status"),
                "metadata_snapshot": candidate.get("metadata") or {},
                "notes": notes or None,
            }
        )
    try:
        records = await repository.add_assets(project_id, snapshots)
    except IntegrityError as exc:
        raise ResearchProjectError("asset_already_linked", "所选成果已存在于当前研究项目") from exc
    return {
        "items": [serialize_project_asset(record, available=True) for record in records],
        "asset_counts": await repository.get_asset_counts(project_id),
    }


async def list_project_assets(
    *,
    project_id: str,
    current_user: User,
    asset_type: str | None,
    query: str | None,
    offset: int,
    limit: int,
) -> dict[str, Any]:
    project = await get_owned_project(project_id, current_user)
    if asset_type:
        _validate_asset_type(asset_type, current_user, require_role=False)
    repository = ResearchProjectRepository()
    records, total = await repository.list_assets(
        project_id=project_id,
        asset_type=asset_type,
        query=query,
        offset=offset,
        limit=limit,
    )
    available = await get_available_project_asset_ids(project, current_user, records, repository=repository)
    return {
        "items": [
            serialize_project_asset(record, available=record.asset_id in available)
            for record in records
        ],
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(records) < total,
    }


async def update_project_asset_notes(
    *,
    project_id: str,
    asset_id: str,
    current_user: User,
    notes: str,
) -> dict[str, Any]:
    project = await get_owned_project(project_id, current_user)
    ensure_project_writable(project)
    repository = ResearchProjectRepository()
    record = await repository.update_asset_notes(
        project_id,
        asset_id,
        notes=notes or None,
    )
    if record is None:
        raise ResearchProjectError("asset_not_found", "项目成果不存在")
    available = await repository.available_reference_ids(
        kb_id=str(project.kb_id),
        uid=str(current_user.uid),
        asset_type=record.asset_type,
        reference_ids=[record.reference_id],
    )
    return serialize_project_asset(record, available=record.reference_id in available)


async def remove_project_asset(
    *,
    project_id: str,
    asset_id: str,
    current_user: User,
) -> dict[str, Any]:
    project = await get_owned_project(project_id, current_user)
    ensure_project_writable(project)
    repository = ResearchProjectRepository()
    record = await repository.remove_asset(project_id, asset_id)
    if record is None:
        raise ResearchProjectError("asset_not_found", "项目成果不存在")
    return {"removed": True, "asset_counts": await repository.get_asset_counts(project_id)}


def ensure_project_writable(project: ResearchProject) -> None:
    if project.status != "active":
        raise ResearchProjectError("project_read_only", "已完成或归档项目需恢复为进行中后才能修改成果")


async def get_available_project_asset_ids(
    project: ResearchProject,
    current_user: User,
    records: list[ResearchProjectAsset],
    *,
    repository: ResearchProjectRepository | None = None,
) -> set[str]:
    grouped: dict[str, list[ResearchProjectAsset]] = defaultdict(list)
    for record in records:
        grouped[record.asset_type].append(record)
    resolved: set[str] = set()
    asset_repository = repository or ResearchProjectRepository()
    for asset_type, type_records in grouped.items():
        if asset_type == "evaluation_experiment" and current_user.role not in {"admin", "superadmin"}:
            continue
        available_references = await asset_repository.available_reference_ids(
            kb_id=str(project.kb_id),
            uid=str(current_user.uid),
            asset_type=asset_type,
            reference_ids=[record.reference_id for record in type_records],
        )
        resolved.update(record.asset_id for record in type_records if record.reference_id in available_references)
    return resolved


def _validate_asset_type(asset_type: str, current_user: User, *, require_role: bool = True) -> None:
    if asset_type not in ASSET_TYPES:
        raise ResearchProjectError("invalid_asset_type", "不支持的研究成果类型")
    if require_role and asset_type == "evaluation_experiment" and current_user.role not in {"admin", "superadmin"}:
        raise ResearchProjectError("forbidden", "只有管理员可以归集消融实验")


__all__ = [
    "PROJECT_STATUSES",
    "ResearchProjectError",
    "add_project_assets",
    "create_research_project",
    "delete_research_project",
    "ensure_project_writable",
    "get_available_project_asset_ids",
    "get_owned_project",
    "get_research_project",
    "list_project_asset_candidates",
    "list_project_assets",
    "list_research_projects",
    "remove_project_asset",
    "serialize_project",
    "serialize_project_activity",
    "serialize_project_asset",
    "update_project_asset_notes",
    "update_research_project",
]
