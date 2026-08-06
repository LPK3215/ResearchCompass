from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

from yuxi.services import research_project_plan_service


@pytest.fixture
def active_project(monkeypatch):
    project = SimpleNamespace(project_id="project-1", status="active")

    async def get_project(project_id, current_user):
        assert project_id == "project-1"
        return project

    monkeypatch.setattr(research_project_plan_service, "get_owned_project", get_project)
    return project


@pytest.mark.asyncio
async def test_create_asset_link_rejects_existing_link_before_insert(monkeypatch, active_project):
    class FakeProjectRepository:
        async def get_asset(self, project_id, asset_id):
            return SimpleNamespace(asset_id=asset_id, asset_type="paper", reference_id="paper-1")

    class FakeRepository:
        async def asset_link_exists(self, project_id, **values):
            assert project_id == "project-1"
            assert values == {
                "asset_id": "asset-1",
                "milestone_id": None,
                "task_id": "task-1",
            }
            return True

        async def create_asset_link(self, *args, **kwargs):
            pytest.fail("existing links must not reach the database insert")

    monkeypatch.setattr(research_project_plan_service, "ResearchProjectPlanRepository", FakeRepository)
    monkeypatch.setattr(research_project_plan_service, "ResearchProjectRepository", FakeProjectRepository)

    with pytest.raises(research_project_plan_service.ResearchProjectError) as exc_info:
        await research_project_plan_service.create_project_plan_asset_link(
            project_id="project-1",
            current_user=SimpleNamespace(uid="user-1"),
            asset_id="asset-1",
            milestone_id=None,
            task_id="task-1",
        )

    assert exc_info.value.error_type == "plan_asset_already_linked"


@pytest.mark.asyncio
async def test_create_asset_link_maps_concurrent_unique_constraint_conflict(monkeypatch, active_project):
    class FakeProjectRepository:
        async def get_asset(self, project_id, asset_id):
            return SimpleNamespace(asset_id=asset_id, asset_type="paper", reference_id="paper-1")

    class FakeRepository:
        async def asset_link_exists(self, project_id, **values):
            return False

        async def create_asset_link(self, *args, **kwargs):
            raise IntegrityError("insert", {}, Exception("duplicate"))

    monkeypatch.setattr(research_project_plan_service, "ResearchProjectPlanRepository", FakeRepository)
    monkeypatch.setattr(research_project_plan_service, "ResearchProjectRepository", FakeProjectRepository)

    with pytest.raises(research_project_plan_service.ResearchProjectError) as exc_info:
        await research_project_plan_service.create_project_plan_asset_link(
            project_id="project-1",
            current_user=SimpleNamespace(uid="user-1"),
            asset_id="asset-1",
            milestone_id=None,
            task_id="task-1",
        )

    assert exc_info.value.error_type == "plan_asset_already_linked"


@pytest.mark.asyncio
async def test_create_asset_link_rejects_invalid_evidence(monkeypatch, active_project):
    class FakeProjectRepository:
        async def get_asset(self, project_id, asset_id):
            return SimpleNamespace(asset_id=asset_id, asset_type="evidence", reference_id="evidence-1")

    class FakePlanRepository:
        async def asset_link_exists(self, *args, **kwargs):
            pytest.fail("unavailable evidence must be rejected before duplicate check")

    async def available_ids(project, current_user, assets):
        return set()

    monkeypatch.setattr(research_project_plan_service, "ResearchProjectRepository", FakeProjectRepository)
    monkeypatch.setattr(research_project_plan_service, "ResearchProjectPlanRepository", FakePlanRepository)
    monkeypatch.setattr(research_project_plan_service, "get_available_project_asset_ids", available_ids)

    with pytest.raises(research_project_plan_service.ResearchProjectError) as exc_info:
        await research_project_plan_service.create_project_plan_asset_link(
            project_id="project-1", current_user=SimpleNamespace(uid="user-1"),
            asset_id="asset-1", milestone_id=None, task_id="task-1",
        )

    assert exc_info.value.error_type == "evidence_unavailable"


@pytest.mark.asyncio
async def test_create_asset_link_allows_verified_evidence(monkeypatch, active_project):
    evidence_asset = SimpleNamespace(
        asset_id="asset-1", asset_type="evidence", reference_id="evidence-1",
        title_snapshot="证据标题", summary_snapshot="证据摘要", status_snapshot="verified",
        metadata_snapshot={}, notes="", added_at=None, updated_at=None,
    )

    class FakeProjectRepository:
        async def get_asset(self, project_id, asset_id):
            return evidence_asset

    class FakePlanRepository:
        async def asset_link_exists(self, *args, **kwargs):
            return False

        async def create_asset_link(self, *args, **kwargs):
            return SimpleNamespace(
                link_id="link-1", asset_id="asset-1", milestone_id=None,
                task_id="task-1", created_at=None,
            )

    async def available_ids(project, current_user, assets):
        return {"asset-1"}

    monkeypatch.setattr(research_project_plan_service, "ResearchProjectRepository", FakeProjectRepository)
    monkeypatch.setattr(research_project_plan_service, "ResearchProjectPlanRepository", FakePlanRepository)
    monkeypatch.setattr(research_project_plan_service, "get_available_project_asset_ids", available_ids)

    result = await research_project_plan_service.create_project_plan_asset_link(
        project_id="project-1", current_user=SimpleNamespace(uid="user-1"),
        asset_id="asset-1", milestone_id=None, task_id="task-1",
    )

    assert result["link_id"] == "link-1"
    assert result["asset"]["available"] is True
