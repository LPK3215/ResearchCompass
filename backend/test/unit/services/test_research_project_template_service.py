from yuxi.services.research_project_template_service import (
    get_project_template,
    list_project_templates,
)


def test_template_catalog_exposes_stable_counts_without_internal_tasks():
    result = list_project_templates()

    assert [item["template_id"] for item in result["items"]] == [
        "systematic-review",
        "empirical-study",
    ]
    assert result["items"][0]["milestone_count"] == 3
    assert result["items"][0]["task_count"] == 6
    assert all("milestones" not in item for item in result["items"])


def test_unknown_template_is_explicitly_missing():
    assert get_project_template("does-not-exist") is None
