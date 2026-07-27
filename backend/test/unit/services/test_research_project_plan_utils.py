from datetime import date
from types import SimpleNamespace

from yuxi.services.research_project_plan_utils import build_project_plan_summary


def _task(*, status: str, due_date: date | None = None):
    return SimpleNamespace(status=status, due_date=due_date)


def _milestone(*, status: str, target_date: date | None = None):
    return SimpleNamespace(status=status, target_date=target_date)


def test_plan_summary_uses_tasks_for_progress_and_reports_explainable_risk_counts():
    summary = build_project_plan_summary(
        project_status="active",
        manual_progress=83,
        milestones=[
            _milestone(status="active", target_date=date(2026, 7, 26)),
            _milestone(status="planned", target_date=date(2026, 8, 2)),
        ],
        tasks=[
            _task(status="done", due_date=date(2026, 7, 20)),
            _task(status="blocked", due_date=date(2026, 8, 1)),
            _task(status="todo", due_date=date(2026, 7, 25)),
            _task(status="in_progress", due_date=date(2026, 8, 20)),
        ],
        today=date(2026, 7, 27),
    )

    assert summary["progress"] == 25
    assert summary["progress_source"] == "tasks"
    assert summary["health"] == "overdue"
    assert summary["next_due_date"] == "2026-07-25"
    assert summary["tasks"] == {
        "total": 4,
        "completed": 1,
        "open": 3,
        "in_progress": 1,
        "blocked": 1,
        "overdue": 1,
        "due_soon": 1,
    }
    assert summary["milestones"]["overdue"] == 1
    assert summary["milestones"]["due_soon"] == 1


def test_plan_summary_keeps_manual_progress_until_execution_items_exist():
    summary = build_project_plan_summary(
        project_status="active",
        manual_progress=62,
        milestones=[],
        tasks=[],
        today=date(2026, 7, 27),
    )

    assert summary["progress"] == 62
    assert summary["progress_source"] == "manual"
    assert summary["health"] == "not_planned"


def test_blocked_task_takes_precedence_as_at_risk_when_nothing_is_overdue():
    summary = build_project_plan_summary(
        project_status="active",
        manual_progress=0,
        milestones=[],
        tasks=[_task(status="blocked", due_date=date(2026, 8, 20))],
        today=date(2026, 7, 27),
    )

    assert summary["health"] == "at_risk"
    assert summary["tasks"]["blocked"] == 1
