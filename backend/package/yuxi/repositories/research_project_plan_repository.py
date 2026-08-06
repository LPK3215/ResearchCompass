"""ResearchCompass 研究项目计划数据访问层。

本模块是本仓库在开源智能体框架 Yuxi 的持久化基础设施之上实现的研究项目计划仓储，
封装里程碑、任务、计划-资产关联的增删改查与排序，并在变更时回写项目进度与活动流；
通用 PostgreSQL 连接池与 ORM 模型基类由 Yuxi 提供，本模块只负责计划业务的读写逻辑。
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import func, select

from yuxi.repositories.research_project_repository import build_project_activity
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_knowledge import (
    ResearchProject,
    ResearchProjectAsset,
    ResearchProjectMilestone,
    ResearchProjectPlanAssetLink,
    ResearchProjectTask,
)
from yuxi.utils.datetime_utils import utc_now_naive


class ResearchProjectPlanWriteConflict(RuntimeError):
    """计划写入期间项目已变为只读。"""


class ResearchProjectPlanRepository:
    async def get_plan_records(
        self, project_id: str
    ) -> tuple[
        list[ResearchProjectMilestone],
        list[ResearchProjectTask],
        list[tuple[ResearchProjectPlanAssetLink, ResearchProjectAsset]],
    ]:
        async with pg_manager.get_async_session_context() as session:
            milestones_result = await session.execute(
                select(ResearchProjectMilestone)
                .where(ResearchProjectMilestone.project_id == project_id)
                .order_by(ResearchProjectMilestone.sort_order, ResearchProjectMilestone.id)
            )
            tasks_result = await session.execute(
                select(ResearchProjectTask)
                .where(ResearchProjectTask.project_id == project_id)
                .order_by(
                    ResearchProjectTask.milestone_id.asc().nulls_last(),
                    ResearchProjectTask.sort_order,
                    ResearchProjectTask.id,
                )
            )
            links_result = await session.execute(
                select(ResearchProjectPlanAssetLink, ResearchProjectAsset)
                .join(ResearchProjectAsset, ResearchProjectAsset.asset_id == ResearchProjectPlanAssetLink.asset_id)
                .where(ResearchProjectPlanAssetLink.project_id == project_id)
                .order_by(ResearchProjectPlanAssetLink.id)
            )
            return (
                list(milestones_result.scalars().all()),
                list(tasks_result.scalars().all()),
                list(links_result.all()),
            )

    async def list_summary_records(
        self, project_ids: list[str]
    ) -> tuple[dict[str, list[ResearchProjectMilestone]], dict[str, list[ResearchProjectTask]]]:
        milestones: dict[str, list[ResearchProjectMilestone]] = defaultdict(list)
        tasks: dict[str, list[ResearchProjectTask]] = defaultdict(list)
        if not project_ids:
            return milestones, tasks
        async with pg_manager.get_async_session_context() as session:
            milestones_result = await session.execute(
                select(ResearchProjectMilestone).where(ResearchProjectMilestone.project_id.in_(project_ids))
            )
            tasks_result = await session.execute(
                select(ResearchProjectTask).where(ResearchProjectTask.project_id.in_(project_ids))
            )
            for milestone in milestones_result.scalars().all():
                milestones[str(milestone.project_id)].append(milestone)
            for task in tasks_result.scalars().all():
                tasks[str(task.project_id)].append(task)
        return milestones, tasks

    async def create_milestone(self, project_id: str, values: dict[str, Any], *, operator_uid: str) -> ResearchProjectMilestone:
        async with pg_manager.get_async_session_context() as session:
            max_order = await session.scalar(
                select(func.max(ResearchProjectMilestone.sort_order)).where(
                    ResearchProjectMilestone.project_id == project_id
                )
            )
            record = ResearchProjectMilestone(
                milestone_id=uuid.uuid4().hex,
                project_id=project_id,
                sort_order=int(max_order if max_order is not None else -1) + 1,
                **values,
            )
            session.add(record)
            session.add(
                build_project_activity(
                    project_id,
                    "milestone_created",
                    operator_uid=operator_uid,
                    reference_id=record.milestone_id,
                    payload={"title": record.title, "status": record.status, "target_date": record.target_date},
                )
            )
            await self._touch_project(session, project_id)
            await session.flush()
            return record

    async def get_milestone(self, project_id: str, milestone_id: str) -> ResearchProjectMilestone | None:
        async with pg_manager.get_async_session_context() as session:
            return await session.scalar(
                select(ResearchProjectMilestone).where(
                    ResearchProjectMilestone.project_id == project_id,
                    ResearchProjectMilestone.milestone_id == milestone_id,
                )
            )

    async def update_milestone(
        self,
        project_id: str,
        milestone_id: str,
        *,
        values: dict[str, Any],
        activity_type: str,
        require_no_open_tasks: bool = False,
        operator_uid: str,
    ) -> tuple[ResearchProjectMilestone | None, int]:
        async with pg_manager.get_async_session_context() as session:
            record = await session.scalar(
                select(ResearchProjectMilestone)
                .where(
                    ResearchProjectMilestone.project_id == project_id,
                    ResearchProjectMilestone.milestone_id == milestone_id,
                )
                .with_for_update(key_share=True)
            )
            if record is None:
                return None, 0
            previous_status = str(record.status)
            if require_no_open_tasks:
                open_task_count = int(
                    await session.scalar(
                        select(func.count())
                        .select_from(ResearchProjectTask)
                        .where(
                            ResearchProjectTask.project_id == project_id,
                            ResearchProjectTask.milestone_id == milestone_id,
                            ResearchProjectTask.status != "done",
                        )
                    )
                    or 0
                )
                if open_task_count:
                    return record, open_task_count
            for key, value in values.items():
                setattr(record, key, value)
            session.add(
                build_project_activity(
                    project_id,
                    activity_type,
                    operator_uid=operator_uid,
                    from_status=previous_status if "status" in values else None,
                    to_status=str(values.get("status")) if "status" in values else None,
                    precondition={"require_no_open_tasks": require_no_open_tasks, "open_task_count": 0},
                    reference_id=record.milestone_id,
                    payload={"title": record.title, "status": record.status, "target_date": record.target_date},
                )
            )
            await self._touch_project(session, project_id)
            await session.flush()
            return record, 0

    async def delete_milestone(self, project_id: str, milestone_id: str, *, operator_uid: str) -> tuple[ResearchProjectMilestone | None, int]:
        async with pg_manager.get_async_session_context() as session:
            record = await session.scalar(
                select(ResearchProjectMilestone)
                .where(
                    ResearchProjectMilestone.project_id == project_id,
                    ResearchProjectMilestone.milestone_id == milestone_id,
                )
                .with_for_update()
            )
            if record is None:
                return None, 0
            task_count = int(
                await session.scalar(
                    select(func.count())
                    .select_from(ResearchProjectTask)
                    .where(
                        ResearchProjectTask.project_id == project_id,
                        ResearchProjectTask.milestone_id == milestone_id,
                    )
                )
                or 0
            )
            if task_count:
                return record, task_count
            session.add(
                build_project_activity(
                    project_id,
                    "milestone_deleted",
                    operator_uid=operator_uid,
                    reference_id=milestone_id,
                    payload={"title": record.title},
                )
            )
            await session.delete(record)
            await self._touch_project(session, project_id)
            return record, 0

    async def reorder_milestones(self, project_id: str, milestone_ids: list[str], *, operator_uid: str) -> bool:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(ResearchProjectMilestone)
                .where(ResearchProjectMilestone.project_id == project_id)
                .with_for_update()
            )
            records = {str(record.milestone_id): record for record in result.scalars().all()}
            if set(records) != set(milestone_ids) or len(milestone_ids) != len(records):
                return False
            for index, milestone_id in enumerate(milestone_ids):
                records[milestone_id].sort_order = index
            session.add(build_project_activity(project_id, "milestones_reordered", operator_uid=operator_uid))
            await self._touch_project(session, project_id)
            await session.flush()
            return True

    async def create_task(self, project_id: str, values: dict[str, Any], *, operator_uid: str) -> ResearchProjectTask | None:
        async with pg_manager.get_async_session_context() as session:
            milestone_id = values.get("milestone_id")
            if milestone_id and not await self._locked_milestone(session, project_id, milestone_id):
                return None
            group_filter = ResearchProjectTask.milestone_id == milestone_id
            if milestone_id is None:
                group_filter = ResearchProjectTask.milestone_id.is_(None)
            max_order = await session.scalar(
                select(func.max(ResearchProjectTask.sort_order)).where(
                    ResearchProjectTask.project_id == project_id,
                    group_filter,
                )
            )
            record = ResearchProjectTask(
                task_id=uuid.uuid4().hex,
                project_id=project_id,
                sort_order=int(max_order if max_order is not None else -1) + 1,
                **values,
            )
            session.add(record)
            await session.flush()
            if milestone_id:
                await self._sync_milestone(session, project_id, milestone_id)
            await self._touch_project(session, project_id, sync_progress=True)
            session.add(
                build_project_activity(
                    project_id,
                    "task_created",
                    operator_uid=operator_uid,
                    reference_id=record.task_id,
                    payload={"title": record.title, "status": record.status, "milestone_id": milestone_id},
                )
            )
            await session.flush()
            return record

    async def get_task(self, project_id: str, task_id: str) -> ResearchProjectTask | None:
        async with pg_manager.get_async_session_context() as session:
            return await session.scalar(
                select(ResearchProjectTask).where(
                    ResearchProjectTask.project_id == project_id,
                    ResearchProjectTask.task_id == task_id,
                )
            )

    async def update_task(
        self,
        project_id: str,
        task_id: str,
        *,
        values: dict[str, Any],
        activity_type: str,
        operator_uid: str,
    ) -> ResearchProjectTask | None:
        async with pg_manager.get_async_session_context() as session:
            record = await session.scalar(
                select(ResearchProjectTask)
                .where(
                    ResearchProjectTask.project_id == project_id,
                    ResearchProjectTask.task_id == task_id,
                )
                .with_for_update()
            )
            if record is None:
                return None
            previous_status = str(record.status)
            old_milestone_id = str(record.milestone_id) if record.milestone_id else None
            new_milestone_id = values.get("milestone_id", old_milestone_id)
            if new_milestone_id and not await self._locked_milestone(session, project_id, new_milestone_id):
                return None
            if new_milestone_id != old_milestone_id:
                group_filter = ResearchProjectTask.milestone_id == new_milestone_id
                if new_milestone_id is None:
                    group_filter = ResearchProjectTask.milestone_id.is_(None)
                max_order = await session.scalar(
                    select(func.max(ResearchProjectTask.sort_order)).where(
                        ResearchProjectTask.project_id == project_id,
                        group_filter,
                    )
                )
                values["sort_order"] = int(max_order if max_order is not None else -1) + 1
            for key, value in values.items():
                setattr(record, key, value)
            await session.flush()
            for milestone_id in {old_milestone_id, new_milestone_id} - {None}:
                await self._sync_milestone(session, project_id, milestone_id)
            await self._touch_project(session, project_id, sync_progress=True)
            session.add(
                build_project_activity(
                    project_id,
                    activity_type,
                    operator_uid=operator_uid,
                    from_status=previous_status if "status" in values else None,
                    to_status=str(values.get("status")) if "status" in values else None,
                    reference_id=record.task_id,
                    payload={
                        "title": record.title,
                        "status": record.status,
                        "priority": record.priority,
                        "milestone_id": record.milestone_id,
                    },
                )
            )
            await session.flush()
            return record

    async def delete_task(self, project_id: str, task_id: str, *, operator_uid: str) -> ResearchProjectTask | None:
        async with pg_manager.get_async_session_context() as session:
            record = await session.scalar(
                select(ResearchProjectTask)
                .where(
                    ResearchProjectTask.project_id == project_id,
                    ResearchProjectTask.task_id == task_id,
                )
                .with_for_update()
            )
            if record is None:
                return None
            milestone_id = str(record.milestone_id) if record.milestone_id else None
            session.add(
                build_project_activity(
                    project_id,
                    "task_deleted",
                    operator_uid=operator_uid,
                    reference_id=task_id,
                    payload={"title": record.title, "milestone_id": milestone_id},
                )
            )
            await session.delete(record)
            await session.flush()
            if milestone_id:
                await self._sync_milestone(session, project_id, milestone_id)
            await self._touch_project(session, project_id, sync_progress=True)
            return record

    async def reorder_tasks(self, project_id: str, milestone_id: str | None, task_ids: list[str], *, operator_uid: str) -> bool:
        async with pg_manager.get_async_session_context() as session:
            group_filter = ResearchProjectTask.milestone_id == milestone_id
            if milestone_id is None:
                group_filter = ResearchProjectTask.milestone_id.is_(None)
            result = await session.execute(
                select(ResearchProjectTask)
                .where(ResearchProjectTask.project_id == project_id, group_filter)
                .with_for_update()
            )
            records = {str(record.task_id): record for record in result.scalars().all()}
            if set(records) != set(task_ids) or len(task_ids) != len(records):
                return False
            for index, task_id in enumerate(task_ids):
                records[task_id].sort_order = index
            session.add(
                build_project_activity(
                    project_id,
                    "tasks_reordered",
                    operator_uid=operator_uid,
                    payload={"milestone_id": milestone_id},
                )
            )
            await self._touch_project(session, project_id)
            await session.flush()
            return True

    async def asset_link_exists(
        self,
        project_id: str,
        *,
        asset_id: str,
        milestone_id: str | None,
        task_id: str | None,
    ) -> bool:
        filters = [
            ResearchProjectPlanAssetLink.project_id == project_id,
            ResearchProjectPlanAssetLink.asset_id == asset_id,
        ]
        if milestone_id:
            filters.append(ResearchProjectPlanAssetLink.milestone_id == milestone_id)
        else:
            filters.append(ResearchProjectPlanAssetLink.task_id == task_id)
        async with pg_manager.get_async_session_context() as session:
            return await session.scalar(select(ResearchProjectPlanAssetLink.id).where(*filters)) is not None

    async def create_asset_link(
        self,
        project_id: str,
        *,
        asset_id: str,
        milestone_id: str | None,
        task_id: str | None,
        operator_uid: str,
    ) -> ResearchProjectPlanAssetLink | None:
        async with pg_manager.get_async_session_context() as session:
            asset = await session.scalar(
                select(ResearchProjectAsset).where(
                    ResearchProjectAsset.project_id == project_id,
                    ResearchProjectAsset.asset_id == asset_id,
                )
            )
            if asset is None:
                return None
            if milestone_id and not await session.scalar(
                select(ResearchProjectMilestone.id).where(
                    ResearchProjectMilestone.project_id == project_id,
                    ResearchProjectMilestone.milestone_id == milestone_id,
                )
            ):
                return None
            if task_id and not await session.scalar(
                select(ResearchProjectTask.id).where(
                    ResearchProjectTask.project_id == project_id,
                    ResearchProjectTask.task_id == task_id,
                )
            ):
                return None
            record = ResearchProjectPlanAssetLink(
                link_id=uuid.uuid4().hex,
                project_id=project_id,
                asset_id=asset_id,
                milestone_id=milestone_id,
                task_id=task_id,
            )
            session.add(record)
            session.add(
                build_project_activity(
                    project_id,
                    "plan_asset_linked",
                    operator_uid=operator_uid,
                    asset_type=asset.asset_type,
                    reference_id=asset.reference_id,
                    payload={
                        "title": asset.title_snapshot,
                        "link_id": record.link_id,
                        "milestone_id": milestone_id,
                        "task_id": task_id,
                    },
                )
            )
            await self._touch_project(session, project_id)
            await session.flush()
            return record

    async def delete_asset_link(
        self, project_id: str, link_id: str, *, operator_uid: str
    ) -> tuple[ResearchProjectPlanAssetLink, ResearchProjectAsset] | None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(ResearchProjectPlanAssetLink, ResearchProjectAsset)
                .join(ResearchProjectAsset, ResearchProjectAsset.asset_id == ResearchProjectPlanAssetLink.asset_id)
                .where(
                    ResearchProjectPlanAssetLink.project_id == project_id,
                    ResearchProjectPlanAssetLink.link_id == link_id,
                )
                .with_for_update()
            )
            row = result.first()
            if row is None:
                return None
            link, asset = row
            session.add(
                build_project_activity(
                    project_id,
                    "plan_asset_unlinked",
                    operator_uid=operator_uid,
                    asset_type=asset.asset_type,
                    reference_id=asset.reference_id,
                    payload={
                        "title": asset.title_snapshot,
                        "milestone_id": link.milestone_id,
                        "task_id": link.task_id,
                    },
                )
            )
            await session.delete(link)
            await self._touch_project(session, project_id)
            return link, asset

    @staticmethod
    async def _locked_milestone(session, project_id: str, milestone_id: str) -> ResearchProjectMilestone | None:
        return await session.scalar(
            select(ResearchProjectMilestone)
            .where(
                ResearchProjectMilestone.project_id == project_id,
                ResearchProjectMilestone.milestone_id == milestone_id,
            )
            .with_for_update()
        )

    @staticmethod
    async def _sync_milestone(session, project_id: str, milestone_id: str) -> None:
        milestone = await session.scalar(
            select(ResearchProjectMilestone)
            .where(
                ResearchProjectMilestone.project_id == project_id,
                ResearchProjectMilestone.milestone_id == milestone_id,
            )
            .with_for_update()
        )
        if milestone is None:
            return
        result = await session.execute(
            select(ResearchProjectTask.status).where(
                ResearchProjectTask.project_id == project_id,
                ResearchProjectTask.milestone_id == milestone_id,
            )
        )
        statuses = list(result.scalars().all())
        if not statuses:
            return
        previous_status = str(milestone.status)
        if all(status == "done" for status in statuses):
            milestone.status = "completed"
            milestone.completed_at = milestone.completed_at or utc_now_naive()
        elif milestone.status == "completed" or any(status != "todo" for status in statuses):
            milestone.status = "active"
            milestone.completed_at = None
        if milestone.status != previous_status:
            session.add(
                build_project_activity(
                    project_id,
                    "milestone_status_changed",
                    reference_id=milestone_id,
                    payload={"title": milestone.title, "status": milestone.status, "automatic": True},
                )
            )

    @staticmethod
    async def _touch_project(session, project_id: str, *, sync_progress: bool = False) -> None:
        project = await session.scalar(
            select(ResearchProject).where(ResearchProject.project_id == project_id).with_for_update()
        )
        if project is None:
            return
        if project.status != "active":
            raise ResearchProjectPlanWriteConflict
        if sync_progress:
            total, completed = (
                await session.execute(
                    select(
                        func.count(ResearchProjectTask.id),
                        func.count(ResearchProjectTask.id).filter(ResearchProjectTask.status == "done"),
                    ).where(ResearchProjectTask.project_id == project_id)
                )
            ).one()
            project.progress = round(int(completed or 0) * 100 / int(total)) if total else 0
        project.updated_at = utc_now_naive()


__all__ = ["ResearchProjectPlanRepository", "ResearchProjectPlanWriteConflict"]
