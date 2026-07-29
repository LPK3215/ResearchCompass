"""ResearchCompass 研究项目执行计划状态与摘要工具。

本模块是本仓库作者为科研项目管理设计的纯计算工具：定义里程碑/任务的状态与优先级
取值集合，并基于里程碑与任务的当前状态计算项目进度、健康度、逾期与即将到期统计。
不涉及任何持久化或框架运行时，仅作为执行计划服务的纯函数辅助层。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any


MILESTONE_STATUSES = {"planned", "active", "completed"}
TASK_STATUSES = {"todo", "in_progress", "blocked", "done"}
TASK_PRIORITIES = {"low", "medium", "high"}


def current_utc_date() -> date:
    return datetime.now(UTC).date()


def build_project_plan_summary(
    *,
    project_status: str,
    manual_progress: int,
    milestones: list[Any],
    tasks: list[Any],
    today: date | None = None,
) -> dict[str, Any]:
    reference_date = today or current_utc_date()
    due_soon_limit = reference_date + timedelta(days=7)
    open_tasks = [task for task in tasks if task.status != "done"]
    open_milestones = [milestone for milestone in milestones if milestone.status != "completed"]
    completed_tasks = len(tasks) - len(open_tasks)
    overdue_tasks = sum(bool(task.due_date and task.due_date < reference_date) for task in open_tasks)
    due_soon_tasks = sum(
        bool(task.due_date and reference_date <= task.due_date <= due_soon_limit) for task in open_tasks
    )
    overdue_milestones = sum(
        bool(milestone.target_date and milestone.target_date < reference_date) for milestone in open_milestones
    )
    due_soon_milestones = sum(
        bool(milestone.target_date and reference_date <= milestone.target_date <= due_soon_limit)
        for milestone in open_milestones
    )
    due_dates = [
        item_date
        for item_date in [
            *(task.due_date for task in open_tasks),
            *(milestone.target_date for milestone in open_milestones),
        ]
        if item_date is not None
    ]
    if tasks:
        progress = round(completed_tasks * 100 / len(tasks))
        progress_source = "tasks"
    else:
        progress = int(manual_progress or 0)
        progress_source = "manual"

    if project_status == "completed":
        health = "completed"
    elif overdue_tasks or overdue_milestones:
        health = "overdue"
    elif any(task.status == "blocked" for task in open_tasks) or due_soon_tasks or due_soon_milestones:
        health = "at_risk"
    elif not tasks and not milestones:
        health = "not_planned"
    else:
        health = "on_track"

    return {
        "progress": progress,
        "progress_source": progress_source,
        "health": health,
        "next_due_date": min(due_dates).isoformat() if due_dates else None,
        "milestones": {
            "total": len(milestones),
            "completed": len(milestones) - len(open_milestones),
            "overdue": overdue_milestones,
            "due_soon": due_soon_milestones,
        },
        "tasks": {
            "total": len(tasks),
            "completed": completed_tasks,
            "open": len(open_tasks),
            "in_progress": sum(task.status == "in_progress" for task in tasks),
            "blocked": sum(task.status == "blocked" for task in open_tasks),
            "overdue": overdue_tasks,
            "due_soon": due_soon_tasks,
        },
    }


__all__ = [
    "MILESTONE_STATUSES",
    "TASK_PRIORITIES",
    "TASK_STATUSES",
    "build_project_plan_summary",
    "current_utc_date",
]
