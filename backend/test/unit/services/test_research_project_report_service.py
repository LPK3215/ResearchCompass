import csv
from io import BytesIO, StringIO
from types import SimpleNamespace

from docx import Document
import pytest

from yuxi.services import research_project_report_service
from yuxi.services.research_project_report_service import render_project_csv, render_project_docx, render_project_markdown


def _report():
    linked_asset = {
        "title": "Evidence snapshot",
        "asset_type": "paper",
        "available": False,
        "notes": "Keep for audit",
    }
    return {
        "project": {
            "title": "Consumer research plan",
            "research_question": "How should the evidence be evaluated?",
            "description": "A complete project report.",
            "status": "active",
            "health": "at_risk",
            "progress": 50,
            "progress_source": "tasks",
            "target_date": "2026-09-01",
            "next_due_date": "2026-08-01",
            "next_action": "Resolve the blocked task",
        },
        "plan": {
            "milestones": [
                {
                    "title": "Evidence review",
                    "status": "active",
                    "progress": 50,
                    "target_date": "2026-08-10",
                    "description": "Review the strongest sources.",
                    "asset_links": [],
                    "tasks": [
                        {
                            "title": "Verify source",
                            "description": "Check provenance.",
                            "status": "blocked",
                            "priority": "high",
                            "due_date": "2026-08-01",
                            "asset_links": [{"asset": linked_asset}],
                        }
                    ],
                }
            ],
            "unassigned_tasks": [],
        },
        "assets": [linked_asset],
        "generated_at": "2026-07-27T10:00:00+00:00",
    }


def test_markdown_report_contains_plan_evidence_and_unavailable_source_marker():
    markdown = render_project_markdown(_report())

    assert "# Consumer research plan" in markdown
    assert "Verify source" in markdown
    assert "Evidence snapshot（源成果已失效）" in markdown
    assert "源成果已失效，仅保留项目快照" in markdown


def test_docx_report_is_openable_and_contains_project_execution_details():
    content = render_project_docx(_report())
    document = Document(BytesIO(content))
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)

    assert content.startswith(b"PK")
    assert "Consumer research plan" in text
    assert "Verify source" in text
    assert "源成果已失效" in text


def test_csv_report_is_flat_and_preserves_project_plan_and_asset_rows():
    content = render_project_csv(_report()).decode("utf-8-sig")
    rows = list(csv.DictReader(StringIO(content)))

    assert [row["record_type"] for row in rows] == ["project", "milestone", "task", "asset"]
    assert rows[0]["title"] == "Consumer research plan"
    assert rows[2]["title"] == "Verify source"
    assert rows[2]["parent_id"]
    assert rows[3]["available"] == "false"


def test_csv_report_escapes_formula_like_user_content():
    report = _report()
    report["project"]["title"] = "=SUM(A1:A2)"
    report["project"]["description"] = "@external-reference"
    report["plan"]["milestones"][0]["tasks"][0]["description"] = "+unsafe-formula"

    content = render_project_csv(report).decode("utf-8-sig")
    rows = list(csv.DictReader(StringIO(content)))

    assert rows[0]["title"] == "'=SUM(A1:A2)"
    assert rows[0]["notes"] == "'@external-reference"
    assert rows[2]["notes"] == "'+unsafe-formula"


@pytest.mark.asyncio
async def test_report_reuses_loaded_project_and_asset_availability_for_plan(monkeypatch):
    project = SimpleNamespace(project_id="project-1")
    asset = SimpleNamespace(asset_id="asset-1", asset_type="paper")
    calls = []

    class FakeRepository:
        async def list_all_assets(self, project_id):
            calls.append(("assets", project_id))
            return [asset]

    async def get_project(project_id, current_user):
        calls.append(("project", project_id))
        return project

    async def get_available(project_arg, current_user, assets, *, repository):
        assert project_arg is project
        assert assets == [asset]
        assert isinstance(repository, FakeRepository)
        calls.append(("availability", project_arg.project_id))
        return {"asset-1"}

    async def get_plan(*, project_id, current_user, project, available_asset_ids):
        calls.append(("plan", project_id, project, available_asset_ids))
        return {"summary": {}, "milestones": [], "unassigned_tasks": []}

    monkeypatch.setattr(research_project_report_service, "ResearchProjectRepository", FakeRepository)
    monkeypatch.setattr(research_project_report_service, "get_owned_project", get_project)
    monkeypatch.setattr(research_project_report_service, "get_available_project_asset_ids", get_available)
    monkeypatch.setattr(research_project_report_service, "get_research_project_plan", get_plan)
    monkeypatch.setattr(research_project_report_service, "serialize_project", lambda *args: {"title": "Report"})
    monkeypatch.setattr(
        research_project_report_service,
        "serialize_project_asset",
        lambda asset, *, available: {"available": available},
    )
    monkeypatch.setattr(research_project_report_service, "render_project_markdown", lambda report: "report")

    filename, content, media_type = await research_project_report_service.export_research_project_report(
        project_id="project-1",
        current_user=SimpleNamespace(uid="user-1"),
        export_format="markdown",
    )

    assert filename == "research-project-project-1.md"
    assert content == b"report"
    assert media_type == "text/markdown; charset=utf-8"
    assert calls == [
        ("project", "project-1"),
        ("assets", "project-1"),
        ("availability", "project-1"),
        ("plan", "project-1", project, {"asset-1"}),
    ]
