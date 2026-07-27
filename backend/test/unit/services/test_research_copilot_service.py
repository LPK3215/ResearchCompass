from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from yuxi.repositories.agent_repository import RESEARCH_COPILOT_AGENT_SLUG
from yuxi.services import research_copilot_service


def _user(uid: str = "user-1") -> SimpleNamespace:
    return SimpleNamespace(uid=uid, role="user", department_id=1)


def _conversation(
    *,
    uid: str = "user-1",
    agent_id: str = RESEARCH_COPILOT_AGENT_SLUG,
    metadata: dict | None = None,
) -> SimpleNamespace:
    now = datetime(2026, 7, 27, tzinfo=UTC)
    return SimpleNamespace(
        thread_id="thread-1",
        uid=uid,
        agent_id=agent_id,
        title="Research project",
        is_pinned=False,
        created_at=now,
        updated_at=now,
        extra_metadata=metadata or {},
    )


@pytest.mark.asyncio
async def test_validate_research_context_enriches_owned_project_scope(monkeypatch):
    async def allow_access(current_user, kb_id):
        assert (current_user.uid, kb_id) == ("user-1", "kb-1")

    class FakeKnowledgeBaseRepository:
        async def get_by_kb_id(self, kb_id):
            assert kb_id == "kb-1"
            return SimpleNamespace(name="Evidence library")

    async def get_owned_project(project_id, current_user):
        assert (project_id, current_user.uid) == ("project-1", "user-1")
        return SimpleNamespace(kb_id="kb-1", title="Grounded review")

    monkeypatch.setattr(research_copilot_service, "_ensure_access", allow_access)
    monkeypatch.setattr(research_copilot_service, "KnowledgeBaseRepository", FakeKnowledgeBaseRepository)
    monkeypatch.setattr(research_copilot_service, "get_owned_project", get_owned_project)

    result = await research_copilot_service.validate_research_context(
        {
            "kb_id": "kb-1",
            "project_id": "project-1",
            "surface": "search",
            "selection": {"type": "paper", "id": "paper-1", "title": "Source paper"},
        },
        current_user=_user(),
    )

    assert result == {
        "kb_id": "kb-1",
        "project_id": "project-1",
        "surface": "search",
        "selection": {"type": "paper", "id": "paper-1", "title": "Source paper"},
        "kb_name": "Evidence library",
        "project_title": "Grounded review",
        "scope_key": "kb-1:project-1",
    }


@pytest.mark.asyncio
async def test_validate_research_context_rejects_invalid_payload():
    with pytest.raises(research_copilot_service.ResearchCopilotError) as exc_info:
        await research_copilot_service.validate_research_context(
            {"kb_id": "kb-1", "unexpected": True},
            current_user=_user(),
        )

    assert exc_info.value.error_type == "invalid_research_context"


@pytest.mark.asyncio
async def test_non_copilot_run_meta_drops_research_context():
    original = {
        "request_id": "request-1",
        "research_context": {"kb_id": "forged-kb", "project_id": "forged-project"},
    }

    result = await research_copilot_service.prepare_research_copilot_run_meta(
        agent_slug="default-chatbot",
        thread_id="thread-1",
        meta=original,
        current_user=_user(),
        db=object(),
    )

    assert result == {"request_id": "request-1"}
    assert original["research_context"]["kb_id"] == "forged-kb"


@pytest.mark.asyncio
async def test_validate_research_context_maps_revoked_access_to_forbidden(monkeypatch):
    async def deny_access(_current_user, _kb_id):
        raise HTTPException(status_code=403, detail="无权访问该知识库")

    monkeypatch.setattr(research_copilot_service, "_ensure_access", deny_access)

    with pytest.raises(research_copilot_service.ResearchCopilotError) as exc_info:
        await research_copilot_service.validate_research_context(
            {"kb_id": "kb-1"},
            current_user=_user(),
        )

    assert exc_info.value.error_type == "forbidden"


@pytest.mark.asyncio
async def test_validate_research_context_rejects_project_from_another_knowledge_base(monkeypatch):
    async def allow_access(_current_user, _kb_id):
        return None

    class FakeKnowledgeBaseRepository:
        async def get_by_kb_id(self, _kb_id):
            return SimpleNamespace(name="Evidence library")

    async def get_owned_project(_project_id, _current_user):
        return SimpleNamespace(kb_id="kb-2", title="Another project")

    monkeypatch.setattr(research_copilot_service, "_ensure_access", allow_access)
    monkeypatch.setattr(research_copilot_service, "KnowledgeBaseRepository", FakeKnowledgeBaseRepository)
    monkeypatch.setattr(research_copilot_service, "get_owned_project", get_owned_project)

    with pytest.raises(research_copilot_service.ResearchCopilotError) as exc_info:
        await research_copilot_service.validate_research_context(
            {"kb_id": "kb-1", "project_id": "project-1"},
            current_user=_user(),
        )

    assert exc_info.value.error_type == "project_scope_mismatch"


@pytest.mark.asyncio
async def test_ensure_research_copilot_thread_reuses_active_scope_thread(monkeypatch):
    context = {
        "kb_id": "kb-1",
        "project_id": "project-1",
        "surface": "projects",
        "selection": None,
        "kb_name": "Evidence library",
        "project_title": "Grounded review",
        "scope_key": "kb-1:project-1",
    }
    conversation = _conversation(metadata={"source": "old"})
    calls: dict[str, object] = {}

    async def validate(payload, *, current_user):
        calls["validation"] = (payload, current_user.uid)
        return context

    class FakeAgentRepository:
        async def ensure_research_copilot_agent(self):
            return SimpleNamespace(slug=RESEARCH_COPILOT_AGENT_SLUG)

    class FakeConversationRepository:
        async def lock_source_scope(self, **kwargs):
            calls["lock"] = kwargs

        async def get_active_by_source_scope(self, **kwargs):
            assert "lock" in calls
            calls["lookup"] = kwargs
            return conversation

        async def create_conversation(self, **_kwargs):
            raise AssertionError("active scope thread should be reused")

        async def update_conversation(self, thread_id, *, metadata):
            calls["update"] = (thread_id, metadata)
            conversation.extra_metadata = metadata
            return conversation

    repository = FakeConversationRepository()
    monkeypatch.setattr(research_copilot_service, "validate_research_context", validate)
    monkeypatch.setattr(research_copilot_service, "AgentRepository", lambda _db: FakeAgentRepository())
    monkeypatch.setattr(research_copilot_service, "ConversationRepository", lambda _db: repository)

    result = await research_copilot_service.ensure_research_copilot_thread(
        payload={"kb_id": "kb-1", "project_id": "project-1"},
        current_user=_user(),
        db=object(),
    )

    assert calls["lookup"] == {
        "uid": "user-1",
        "agent_id": RESEARCH_COPILOT_AGENT_SLUG,
        "source": research_copilot_service.RESEARCH_COPILOT_SOURCE,
        "scope_key": "kb-1:project-1",
    }
    assert calls["lock"] == calls["lookup"]
    assert calls["update"] == (
        "thread-1",
        {
            "source": research_copilot_service.RESEARCH_COPILOT_SOURCE,
            "research_scope_key": "kb-1:project-1",
            "research_context": context,
        },
    )
    assert result["thread"]["id"] == "thread-1"
    assert result["research_context"] == context


@pytest.mark.asyncio
async def test_ensure_research_copilot_thread_serializes_concurrent_scope_creation(monkeypatch):
    context = {
        "kb_id": "kb-1",
        "project_id": "project-1",
        "surface": "projects",
        "selection": None,
        "kb_name": "Evidence library",
        "project_title": "Grounded review",
        "scope_key": "kb-1:project-1",
    }
    metadata = {
        "source": research_copilot_service.RESEARCH_COPILOT_SOURCE,
        "research_scope_key": context["scope_key"],
        "research_context": context,
    }
    conversation = None
    scope_lock = asyncio.Lock()
    calls: dict[str, list] = {"create": [], "update": []}

    async def validate(_payload, *, current_user):
        assert current_user.uid == "user-1"
        return context

    class FakeAgentRepository:
        async def ensure_research_copilot_agent(self):
            return SimpleNamespace(slug=RESEARCH_COPILOT_AGENT_SLUG)

    class FakeConversationRepository:
        async def lock_source_scope(self, **kwargs):
            assert kwargs == {
                "uid": "user-1",
                "agent_id": RESEARCH_COPILOT_AGENT_SLUG,
                "source": research_copilot_service.RESEARCH_COPILOT_SOURCE,
                "scope_key": "kb-1:project-1",
            }
            await scope_lock.acquire()

        async def get_active_by_source_scope(self, **_kwargs):
            assert scope_lock.locked()
            return conversation

        async def create_conversation(self, **kwargs):
            nonlocal conversation
            assert scope_lock.locked()
            assert "thread_id" not in kwargs
            calls["create"].append(kwargs)
            conversation = _conversation(metadata=kwargs["metadata"])
            scope_lock.release()
            return conversation

        async def update_conversation(self, thread_id, *, metadata):
            assert scope_lock.locked()
            calls["update"].append((thread_id, metadata))
            conversation.extra_metadata = metadata
            scope_lock.release()
            return conversation

    monkeypatch.setattr(research_copilot_service, "validate_research_context", validate)
    monkeypatch.setattr(research_copilot_service, "AgentRepository", lambda _db: FakeAgentRepository())
    repository = FakeConversationRepository()
    monkeypatch.setattr(research_copilot_service, "ConversationRepository", lambda _db: repository)

    first, second = await asyncio.gather(
        *(
            research_copilot_service.ensure_research_copilot_thread(
                payload={"kb_id": "kb-1", "project_id": "project-1"},
                current_user=_user(),
                db=object(),
            )
            for _ in range(2)
        )
    )

    assert len(calls["create"]) == 1
    assert calls["create"][0]["uid"] == "user-1"
    assert calls["create"][0]["metadata"] == metadata
    assert calls["update"] == [("thread-1", metadata)]
    assert first["thread"]["id"] == second["thread"]["id"] == "thread-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "expected_error"),
    [
        ("missing", "thread_not_found"),
        ("other_user", "thread_not_found"),
        ("other_agent", "thread_not_found"),
        ("forged_source", "invalid_copilot_thread"),
    ],
)
async def test_prepare_research_copilot_run_meta_rejects_forged_or_unauthorized_thread(
    monkeypatch,
    case,
    expected_error,
):
    valid_metadata = {
        "source": research_copilot_service.RESEARCH_COPILOT_SOURCE,
        "research_context": {"kb_id": "kb-1"},
    }
    conversations = {
        "missing": None,
        "other_user": _conversation(uid="user-2", metadata=valid_metadata),
        "other_agent": _conversation(agent_id="default-chatbot", metadata=valid_metadata),
        "forged_source": _conversation(metadata={**valid_metadata, "source": "chat"}),
    }

    class FakeConversationRepository:
        async def get_conversation_by_thread_id(self, thread_id):
            assert thread_id == "thread-1"
            return conversations[case]

    monkeypatch.setattr(
        research_copilot_service,
        "ConversationRepository",
        lambda _db: FakeConversationRepository(),
    )

    with pytest.raises(research_copilot_service.ResearchCopilotError) as exc_info:
        await research_copilot_service.prepare_research_copilot_run_meta(
            agent_slug=RESEARCH_COPILOT_AGENT_SLUG,
            thread_id="thread-1",
            meta={"research_context": {"kb_id": "attacker-kb"}},
            current_user=_user(),
            db=object(),
        )

    assert exc_info.value.error_type == expected_error


@pytest.mark.asyncio
async def test_prepare_research_copilot_run_meta_revalidates_persisted_context(monkeypatch):
    scope = {"kb_id": "kb-1", "project_id": "project-1", "surface": "projects"}
    persisted_context = {
        **scope,
        "kb_name": "Evidence library",
        "project_title": "Grounded review",
        "scope_key": "kb-1:project-1",
    }
    canonical_context = {**persisted_context}
    conversation = _conversation(
        metadata={
            "source": research_copilot_service.RESEARCH_COPILOT_SOURCE,
            "research_context": persisted_context,
        }
    )
    captured: dict[str, object] = {}

    class FakeConversationRepository:
        async def get_conversation_by_thread_id(self, _thread_id):
            return conversation

    async def validate(payload, *, current_user):
        captured["validation"] = (payload, current_user.uid)
        return canonical_context

    monkeypatch.setattr(
        research_copilot_service,
        "ConversationRepository",
        lambda _db: FakeConversationRepository(),
    )
    monkeypatch.setattr(research_copilot_service, "validate_research_context", validate)

    result = await research_copilot_service.prepare_research_copilot_run_meta(
        agent_slug=RESEARCH_COPILOT_AGENT_SLUG,
        thread_id="thread-1",
        meta={"trace_id": "trace-1", "research_context": {"kb_id": "attacker-kb"}},
        current_user=_user(),
        db=object(),
    )

    assert captured["validation"] == (scope, "user-1")
    assert result == {
        "trace_id": "trace-1",
        "source": research_copilot_service.RESEARCH_COPILOT_SOURCE,
        "research_context": canonical_context,
    }


@pytest.mark.asyncio
async def test_prepare_research_copilot_run_meta_revalidates_enriched_thread_context(monkeypatch):
    conversation = _conversation(
        metadata={
            "source": research_copilot_service.RESEARCH_COPILOT_SOURCE,
            "research_context": {
                "kb_id": "kb-1",
                "project_id": None,
                "surface": "synthesis",
                "selection": None,
                "kb_name": "Stored knowledge base name",
                "project_title": "",
                "scope_key": "kb-1:library",
            },
        }
    )

    class FakeConversationRepository:
        async def get_conversation_by_thread_id(self, _thread_id):
            return conversation

    class FakeKnowledgeBaseRepository:
        async def get_by_kb_id(self, kb_id):
            assert kb_id == "kb-1"
            return SimpleNamespace(name="Current knowledge base name")

    async def allow_access(current_user, kb_id):
        assert (current_user.uid, kb_id) == ("user-1", "kb-1")

    monkeypatch.setattr(
        research_copilot_service,
        "ConversationRepository",
        lambda _db: FakeConversationRepository(),
    )
    monkeypatch.setattr(research_copilot_service, "KnowledgeBaseRepository", FakeKnowledgeBaseRepository)
    monkeypatch.setattr(research_copilot_service, "_ensure_access", allow_access)

    result = await research_copilot_service.prepare_research_copilot_run_meta(
        agent_slug=RESEARCH_COPILOT_AGENT_SLUG,
        thread_id="thread-1",
        meta={},
        current_user=_user(),
        db=object(),
    )

    assert result["research_context"] == {
        "kb_id": "kb-1",
        "project_id": None,
        "surface": "synthesis",
        "selection": None,
        "kb_name": "Current knowledge base name",
        "project_title": "",
        "scope_key": "kb-1:library",
    }
