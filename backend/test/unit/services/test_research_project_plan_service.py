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
    class FakeRepository:
        async def asset_link_exists(self, project_id, **values):
            return False

        async def create_asset_link(self, *args, **kwargs):
            raise IntegrityError("insert", {}, Exception("duplicate"))

    monkeypatch.setattr(research_project_plan_service, "ResearchProjectPlanRepository", FakeRepository)

    with pytest.raises(research_project_plan_service.ResearchProjectError) as exc_info:
        await research_project_plan_service.create_project_plan_asset_link(
            project_id="project-1",
            current_user=SimpleNamespace(uid="user-1"),
            asset_id="asset-1",
            milestone_id=None,
            task_id="task-1",
        )

    assert exc_info.value.error_type == "plan_asset_already_linked"
