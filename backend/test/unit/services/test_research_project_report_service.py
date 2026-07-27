from io import BytesIO

from docx import Document

from yuxi.services.research_project_report_service import render_project_docx, render_project_markdown


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
