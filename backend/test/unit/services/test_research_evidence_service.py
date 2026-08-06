from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from yuxi.services import research_evidence_service


class _Session:
    def __init__(self, row):
        self.row = row
        self.added = []

    async def scalar(self, statement):
        return self.row

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        return None


class _ActivityResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return self

    def all(self):
        return self._items


class _CitationResult:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class _ImpactSession(_Session):
    def __init__(self, row, impacts):
        super().__init__(row)
        self.impacts = impacts

    async def execute(self, statement):
        return _CitationResult(self.impacts)


class _ActivitySession(_Session):
    def __init__(self, row, activities):
        super().__init__(row)
        self.activities = activities

    async def execute(self, statement):
        return _ActivityResult(self.activities)


class _SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *args):
        return False


@pytest.mark.asyncio
async def test_create_evidence_rejects_chunk_from_another_database(monkeypatch):
    class FakeChunks:
        async def list_by_chunk_ids(self, chunk_ids):
            return [SimpleNamespace(chunk_id=chunk_ids[0], kb_id="other-kb", content="private")]

    async def allow_access(current_user, kb_id):
        return None

    monkeypatch.setattr(research_evidence_service, "KnowledgeChunkRepository", lambda: FakeChunks())
    monkeypatch.setattr(research_evidence_service, "_ensure_access", allow_access)

    with pytest.raises(HTTPException) as exc_info:
        await research_evidence_service.create_evidence(
            kb_id="requested-kb",
            chunk_id="chunk-1",
            current_user=SimpleNamespace(uid="user-1"),
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_archived_evidence_cannot_transition_back_to_verified(monkeypatch):
    row = SimpleNamespace(
        evidence_id="evidence-1",
        kb_id="kb-1",
        source_chunk_id="chunk-1",
        content_snapshot="snapshot",
        status="archived",
        invalid_reason=None,
        created_by="user-1",
        verified_by=None,
        verified_at=None,
        archived_by="user-1",
        archived_at=None,
    )
    session = _Session(row)

    async def allow_access(current_user, kb_id):
        assert kb_id == "kb-1"

    monkeypatch.setattr(research_evidence_service, "_ensure_access", allow_access)
    monkeypatch.setattr(
        research_evidence_service.pg_manager,
        "get_async_session_context",
        lambda: _SessionContext(session),
    )

    with pytest.raises(HTTPException) as exc_info:
        await research_evidence_service.transition_evidence(
            evidence_id="evidence-1",
            status="verified",
            reason=None,
            current_user=SimpleNamespace(uid="user-2"),
        )

    assert exc_info.value.status_code == 409
    assert session.added == []


@pytest.mark.asyncio
async def test_evidence_lifecycle_records_operator_timestamps_and_reasons(monkeypatch):
    row = SimpleNamespace(
        evidence_id="evidence-1", kb_id="kb-1", source_chunk_id="chunk-1",
        content_snapshot="snapshot", status="unverified", invalid_reason=None,
        created_by="user-1", verified_by=None, verified_at=None,
        archived_by=None, archived_at=None,
    )
    session = _ActivitySession(row, [])

    async def allow_access(current_user, kb_id):
        assert kb_id == "kb-1"

    monkeypatch.setattr(research_evidence_service, "_ensure_access", allow_access)
    monkeypatch.setattr(
        research_evidence_service.pg_manager,
        "get_async_session_context",
        lambda: _SessionContext(session),
    )

    verified = await research_evidence_service.transition_evidence(
        evidence_id="evidence-1", status="verified", reason="人工核验",
        current_user=SimpleNamespace(uid="reviewer-1"),
    )
    assert verified["status"] == "verified"
    assert verified["verified_by"] == "reviewer-1"
    assert verified["verified_at"] is not None
    assert session.added[-1].from_status == "unverified"

    invalid = await research_evidence_service.transition_evidence(
        evidence_id="evidence-1", status="invalid", reason="来源已撤回",
        current_user=SimpleNamespace(uid="reviewer-2"),
    )
    assert invalid["status"] == "invalid"
    assert invalid["invalid_reason"] == "来源已撤回"
    assert session.added[-1].operator_uid == "reviewer-2"
    assert session.added[-1].from_status == "verified"

    archived = await research_evidence_service.transition_evidence(
        evidence_id="evidence-1", status="archived", reason="归档留痕",
        current_user=SimpleNamespace(uid="reviewer-3"),
    )
    assert archived["status"] == "archived"
    assert archived["archived_by"] == "reviewer-3"
    assert archived["archived_at"] is not None
    assert [item.to_status for item in session.added] == ["verified", "invalid", "archived"]


@pytest.mark.asyncio
async def test_evidence_activity_listing_preserves_transition_order(monkeypatch):
    row = SimpleNamespace(evidence_id="evidence-1", kb_id="kb-1")
    activities = [
        SimpleNamespace(activity_id="a1", operator_uid="u1", from_status="none", to_status="unverified",
                         reason="evidence_created", created_at=SimpleNamespace(isoformat=lambda: "2026-01-01T00:00:00")),
        SimpleNamespace(activity_id="a2", operator_uid="u2", from_status="unverified", to_status="verified",
                         reason="人工核验", created_at=SimpleNamespace(isoformat=lambda: "2026-01-01T00:01:00")),
    ]
    session = _ActivitySession(row, activities)

    async def allow_access(current_user, kb_id):
        return None

    monkeypatch.setattr(research_evidence_service, "_ensure_access", allow_access)
    monkeypatch.setattr(
        research_evidence_service.pg_manager,
        "get_async_session_context",
        lambda: _SessionContext(session),
    )
    result = await research_evidence_service.list_evidence_activities(
        evidence_id="evidence-1", current_user=SimpleNamespace(uid="u1")
    )
    assert [item["to_status"] for item in result] == ["unverified", "verified"]
    assert result[1]["operator_uid"] == "u2"
    assert result[1]["reason"] == "人工核验"


@pytest.mark.asyncio
async def test_evidence_citation_listing_returns_auditable_synthesis_reference(monkeypatch):
    evidence = SimpleNamespace(evidence_id="evidence-1", kb_id="kb-1")
    citation = SimpleNamespace(
        citation_id="citation-1", evidence_id="evidence-1", synthesis_run_id="run-1",
        cited_by="reviewer-1", cited_at=SimpleNamespace(isoformat=lambda: "2026-01-01T00:02:00"), id=1,
    )
    synthesis = SimpleNamespace(status="success")

    class CitationSession(_Session):
        async def execute(self, statement):
            return _CitationResult([(citation, synthesis)])

    session = CitationSession(evidence)

    async def allow_access(current_user, kb_id):
        assert kb_id == "kb-1"

    monkeypatch.setattr(research_evidence_service, "_ensure_access", allow_access)
    monkeypatch.setattr(
        research_evidence_service.pg_manager,
        "get_async_session_context",
        lambda: _SessionContext(session),
    )

    result = await research_evidence_service.list_evidence_citations(
        evidence_id="evidence-1", current_user=SimpleNamespace(uid="reviewer-1")
    )

    assert result == [{
        "citation_id": "citation-1", "evidence_id": "evidence-1", "synthesis_run_id": "run-1",
        "synthesis_status": "success", "cited_by": "reviewer-1", "cited_at": "2026-01-01T00:02:00",
    }]


@pytest.mark.asyncio
async def test_evidence_impacts_returns_milestone_and_task_targets(monkeypatch):
    evidence = SimpleNamespace(evidence_id="evidence-1", kb_id="kb-1")
    impacts = [
        ("project-1", "项目一", "link-m", "milestone-1", "里程碑一", None, None),
        ("project-1", "项目一", "link-t", None, None, "task-1", "任务一"),
    ]
    session = _ImpactSession(evidence, impacts)

    async def allow_access(current_user, kb_id):
        assert kb_id == "kb-1"

    monkeypatch.setattr(research_evidence_service, "_ensure_access", allow_access)
    monkeypatch.setattr(
        research_evidence_service.pg_manager,
        "get_async_session_context",
        lambda: _SessionContext(session),
    )
    result = await research_evidence_service.list_evidence_impacts(
        evidence_id="evidence-1", current_user=SimpleNamespace(uid="user-1")
    )
    assert result[0]["target_type"] == "milestone"
    assert result[1]["target_type"] == "task"
    assert result[1]["target_id"] == "task-1"


@pytest.mark.asyncio
async def test_evidence_impacts_does_not_bypass_knowledge_base_access(monkeypatch):
    evidence = SimpleNamespace(evidence_id="evidence-1", kb_id="private-kb")
    session = _ImpactSession(evidence, [])

    async def deny_access(current_user, kb_id):
        raise HTTPException(status_code=404, detail="知识库不存在")

    monkeypatch.setattr(research_evidence_service, "_ensure_access", deny_access)
    monkeypatch.setattr(
        research_evidence_service.pg_manager,
        "get_async_session_context",
        lambda: _SessionContext(session),
    )
    with pytest.raises(HTTPException) as exc_info:
        await research_evidence_service.list_evidence_impacts(
            evidence_id="evidence-1", current_user=SimpleNamespace(uid="outsider")
        )
    assert exc_info.value.status_code == 404
