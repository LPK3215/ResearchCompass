from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from yuxi.services import research_project_service


def _user(*, role: str = "user", uid: str = "user-1") -> SimpleNamespace:
    return SimpleNamespace(uid=uid, role=role, department_id=1)


def _project(*, status: str = "active", uid: str = "user-1") -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        project_id="project-1",
        kb_id="kb-1",
        uid=uid,
        title="Grounded literature review",
        research_question="Which evidence supports the research question?",
        description="Project description",
        status=status,
        progress=40,
        next_action="Review the strongest evidence",
        tags=["evidence"],
        target_date=None,
        created_at=None,
        updated_at=None,
        completed_at=None,
        archived_at=None,
    )


def _asset(*, asset_type: str = "paper", reference_id: str = "paper-1") -> SimpleNamespace:
    return SimpleNamespace(
        asset_id="asset-1",
        asset_type=asset_type,
        reference_id=reference_id,
        title_snapshot="Evidence-grounded paper",
        summary_snapshot="Verified source snapshot",
        status_snapshot="verified",
        metadata_snapshot={},
        notes="Important source",
        added_at=None,
        updated_at=None,
    )


def _empty_counts() -> dict[str, int]:
    return {**{asset_type: 0 for asset_type in research_project_service.ASSET_TYPES}, "total": 0}


@pytest.mark.asyncio
async def test_update_project_applies_lifecycle_timestamps(monkeypatch):
    project = _project()
    captured = []

    class FakeRepository:
        async def get(self, project_id, *, uid):
            assert (project_id, uid) == ("project-1", "user-1")
            return project

        async def update(self, project_id, *, uid, values, activity_type):
            captured.append((dict(values), activity_type))
            for key, value in values.items():
                setattr(project, key, value)
            return project

        async def get_asset_counts(self, project_id):
            return _empty_counts()

    async def allow_access(current_user, kb_id):
        assert kb_id == "kb-1"

    repository = FakeRepository()
    monkeypatch.setattr(research_project_service, "ResearchProjectRepository", lambda: repository)
    monkeypatch.setattr(research_project_service, "_ensure_access", allow_access)

    completed = await research_project_service.update_research_project(
        project_id="project-1",
        current_user=_user(),
        values={"status": "completed"},
    )

    assert completed["status"] == "completed"
    assert completed["progress"] == 100
    assert completed["completed_at"] is not None
    assert captured[-1][1] == "project_status_changed"

    reactivated = await research_project_service.update_research_project(
        project_id="project-1",
        current_user=_user(),
        values={"status": "active", "progress": 60},
    )

    assert reactivated["status"] == "active"
    assert reactivated["progress"] == 60
    assert reactivated["completed_at"] is None
    assert reactivated["archived_at"] is None


@pytest.mark.asyncio
async def test_archived_project_rejects_metadata_changes(monkeypatch):
    class FakeRepository:
        async def get(self, project_id, *, uid):
            return _project(status="archived")

    async def allow_access(current_user, kb_id):
        return None

    monkeypatch.setattr(research_project_service, "ResearchProjectRepository", FakeRepository)
    monkeypatch.setattr(research_project_service, "_ensure_access", allow_access)

    with pytest.raises(research_project_service.ResearchProjectError) as exc_info:
        await research_project_service.update_research_project(
            project_id="project-1",
            current_user=_user(),
            values={"title": "Changed after archive"},
        )

    assert exc_info.value.error_type == "project_archived"


@pytest.mark.asyncio
async def test_owned_project_rechecks_revoked_knowledge_base_access(monkeypatch):
    class FakeRepository:
        async def get(self, project_id, *, uid):
            return _project()

    async def deny_access(current_user, kb_id):
        raise HTTPException(status_code=403, detail="revoked")

    monkeypatch.setattr(research_project_service, "ResearchProjectRepository", FakeRepository)
    monkeypatch.setattr(research_project_service, "_ensure_access", deny_access)

    with pytest.raises(research_project_service.ResearchProjectError) as exc_info:
        await research_project_service.get_research_project(project_id="project-1", current_user=_user())

    assert exc_info.value.error_type == "forbidden"


@pytest.mark.asyncio
async def test_owned_project_does_not_mask_access_infrastructure_failures(monkeypatch):
    class FakeRepository:
        async def get(self, project_id, *, uid):
            return _project()

    async def fail_access(current_user, kb_id):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(research_project_service, "ResearchProjectRepository", FakeRepository)
    monkeypatch.setattr(research_project_service, "_ensure_access", fail_access)

    with pytest.raises(RuntimeError, match="database unavailable"):
        await research_project_service.get_research_project(project_id="project-1", current_user=_user())


@pytest.mark.asyncio
async def test_completed_project_progress_remains_complete_when_metadata_changes(monkeypatch):
    project = _project(status="completed")
    project.progress = 100

    class FakeRepository:
        async def get(self, project_id, *, uid):
            return project

        async def update(self, project_id, *, uid, values, activity_type):
            for key, value in values.items():
                setattr(project, key, value)
            return project

        async def get_asset_counts(self, project_id):
            return _empty_counts()

    async def allow_access(current_user, kb_id):
        return None

    monkeypatch.setattr(research_project_service, "ResearchProjectRepository", FakeRepository)
    monkeypatch.setattr(research_project_service, "_ensure_access", allow_access)

    result = await research_project_service.update_research_project(
        project_id="project-1",
        current_user=_user(),
        values={"description": "Updated summary", "progress": 35},
    )

    assert result["description"] == "Updated summary"
    assert result["progress"] == 100


@pytest.mark.parametrize("asset_type", sorted(research_project_service.ASSET_TYPES - {"evaluation_experiment"}))
def test_standard_user_can_manage_personal_project_asset_types(asset_type):
    research_project_service._validate_asset_type(asset_type, _user())


def test_standard_user_cannot_collect_evaluation_experiments():
    with pytest.raises(research_project_service.ResearchProjectError) as exc_info:
        research_project_service._validate_asset_type("evaluation_experiment", _user())

    assert exc_info.value.error_type == "forbidden"
    research_project_service._validate_asset_type("evaluation_experiment", _user(role="admin"))


@pytest.mark.asyncio
async def test_add_assets_resolves_batch_and_preserves_request_order(monkeypatch):
    calls = []

    class FakeRepository:
        async def get(self, project_id, *, uid):
            return _project()

        async def get_linked_reference_ids(self, project_id, asset_type):
            return set()

        async def get_candidates(self, **kwargs):
            calls.append(kwargs)
            return {
                "paper-2": {
                    "reference_id": "paper-2",
                    "title": "Second paper",
                    "summary": "Second summary",
                    "status": "verified",
                    "metadata": {"year": 2025},
                },
                "paper-1": {
                    "reference_id": "paper-1",
                    "title": "First paper",
                    "summary": "First summary",
                    "status": "verified",
                    "metadata": {"year": 2024},
                },
            }

        async def add_assets(self, project_id, assets):
            assert [item["reference_id"] for item in assets] == ["paper-1", "paper-2"]
            return [_asset(reference_id=item["reference_id"]) for item in assets]

        async def get_asset_counts(self, project_id):
            return {**_empty_counts(), "paper": 2, "total": 2}

    async def allow_access(current_user, kb_id):
        return None

    repository = FakeRepository()
    monkeypatch.setattr(research_project_service, "ResearchProjectRepository", lambda: repository)
    monkeypatch.setattr(research_project_service, "_ensure_access", allow_access)

    result = await research_project_service.add_project_assets(
        project_id="project-1",
        current_user=_user(),
        asset_type="paper",
        reference_ids=["paper-1", "paper-2"],
        notes="Shared note",
    )

    assert len(calls) == 1
    assert calls[0]["reference_ids"] == ["paper-1", "paper-2"]
    assert [item["reference_id"] for item in result["items"]] == ["paper-1", "paper-2"]
    assert result["asset_counts"]["total"] == 2


@pytest.mark.asyncio
async def test_add_assets_maps_unique_constraint_race_to_business_error(monkeypatch):
    class FakeRepository:
        async def get(self, project_id, *, uid):
            return _project()

        async def get_linked_reference_ids(self, project_id, asset_type):
            return set()

        async def get_candidates(self, **kwargs):
            return {
                "paper-1": {
                    "reference_id": "paper-1",
                    "title": "Paper",
                    "summary": "Summary",
                    "status": "verified",
                    "metadata": {},
                }
            }

        async def add_assets(self, project_id, assets):
            raise IntegrityError("insert", {}, Exception("duplicate"))

    async def allow_access(current_user, kb_id):
        return None

    monkeypatch.setattr(research_project_service, "ResearchProjectRepository", FakeRepository)
    monkeypatch.setattr(research_project_service, "_ensure_access", allow_access)

    with pytest.raises(research_project_service.ResearchProjectError) as exc_info:
        await research_project_service.add_project_assets(
            project_id="project-1",
            current_user=_user(),
            asset_type="paper",
            reference_ids=["paper-1"],
            notes="",
        )

    assert exc_info.value.error_type == "asset_already_linked"


@pytest.mark.asyncio
async def test_completed_project_rejects_asset_mutations(monkeypatch):
    class FakeRepository:
        async def get(self, project_id, *, uid):
            return _project(status="completed")

    async def allow_access(current_user, kb_id):
        return None

    monkeypatch.setattr(research_project_service, "ResearchProjectRepository", FakeRepository)
    monkeypatch.setattr(research_project_service, "_ensure_access", allow_access)

    with pytest.raises(research_project_service.ResearchProjectError) as exc_info:
        await research_project_service.remove_project_asset(
            project_id="project-1",
            asset_id="asset-1",
            current_user=_user(),
        )

    assert exc_info.value.error_type == "project_read_only"


@pytest.mark.asyncio
async def test_update_asset_notes_reports_deleted_source_as_unavailable(monkeypatch):
    record = _asset()

    class FakeRepository:
        async def get(self, project_id, *, uid):
            return _project()

        async def update_asset_notes(self, project_id, asset_id, *, notes):
            record.notes = notes
            return record

        async def available_reference_ids(self, **kwargs):
            return set()

    async def allow_access(current_user, kb_id):
        return None

    monkeypatch.setattr(research_project_service, "ResearchProjectRepository", FakeRepository)
    monkeypatch.setattr(research_project_service, "_ensure_access", allow_access)

    result = await research_project_service.update_project_asset_notes(
        project_id="project-1",
        asset_id="asset-1",
        current_user=_user(),
        notes="Keep the snapshot for the audit trail",
    )

    assert result["notes"] == "Keep the snapshot for the audit trail"
    assert result["available"] is False


@pytest.mark.asyncio
async def test_project_list_uses_per_project_counts_and_pagination(monkeypatch):
    first = _project()
    second = _project()
    second.project_id = "project-2"
    second.title = "Second project"
    first_counts = {**_empty_counts(), "paper": 2, "total": 2}
    second_counts = {**_empty_counts(), "search_run": 1, "total": 1}

    class FakeRepository:
        async def list_for_user(self, **kwargs):
            assert kwargs["uid"] == "user-1"
            assert kwargs["kb_id"] == "kb-1"
            return [first, second], 3, {"project-1": first_counts, "project-2": second_counts}

    async def allow_access(current_user, kb_id):
        return None

    monkeypatch.setattr(research_project_service, "ResearchProjectRepository", FakeRepository)
    monkeypatch.setattr(research_project_service, "_ensure_access", allow_access)

    result = await research_project_service.list_research_projects(
        kb_id="kb-1",
        current_user=_user(),
        status=None,
        query=None,
        offset=0,
        limit=2,
    )

    assert result["items"][0]["asset_counts"]["paper"] == 2
    assert result["items"][1]["asset_counts"]["search_run"] == 1
    assert result["total"] == 3
    assert result["has_more"] is True
