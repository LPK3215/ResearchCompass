from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from yuxi.repositories.conversation_repository import INVOCATION_CONVERSATION_SOURCES
from yuxi.services import conversation_service

pytestmark = pytest.mark.unit
GENERAL_CHAT_EXPECTED_EXCLUDED_SOURCES = (*INVOCATION_CONVERSATION_SOURCES, "research_copilot")


class _ConversationRepository:
    def __init__(self, _db: Any):
        self.calls: list[dict[str, Any]] = []

    async def list_conversations(self, **kwargs: Any) -> list[Any]:
        self.calls.append(kwargs)
        return []

    async def search_conversations_by_message_content(self, **kwargs: Any) -> tuple[list[Any], bool]:
        self.calls.append(kwargs)
        return [], False


@pytest.mark.asyncio
async def test_create_thread_strips_research_copilot_reserved_metadata(
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict[str, Any] = {}
    now = datetime(2026, 7, 27)
    user = SimpleNamespace(uid="user-a")
    agent = SimpleNamespace(slug="research-copilot", backend_id="ChatbotAgent")

    class _Result:
        def scalar_one_or_none(self):
            return user

    class _Db:
        async def execute(self, _query):
            return _Result()

    class _AgentRepository:
        def __init__(self, _db):
            pass

        async def get_visible_by_slug(self, **_kwargs):
            return agent

    class _CreateConversationRepository:
        def __init__(self, _db):
            pass

        async def create_conversation(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                thread_id=kwargs["thread_id"],
                uid=kwargs["uid"],
                agent_id=kwargs["agent_id"],
                title=kwargs["title"],
                created_at=now,
                updated_at=now,
                extra_metadata=kwargs["metadata"],
            )

    monkeypatch.setattr(conversation_service, "AgentRepository", _AgentRepository)
    monkeypatch.setattr(conversation_service, "ConversationRepository", _CreateConversationRepository)

    result = await conversation_service.create_thread_view(
        agent_slug="research-copilot",
        title="Generic thread",
        metadata={
            "source": "research_copilot",
            "research_scope_key": "kb:kb-a:project-a",
            "research_context": {"kb_id": "kb-a", "project_id": "project-a"},
            "custom": "preserved",
        },
        db=_Db(),
        current_uid="user-a",
    )

    assert captured["metadata"] == {
        "custom": "preserved",
        "backend_id": "ChatbotAgent",
    }
    assert result["metadata"] == captured["metadata"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("agent_id", "expected_sources"),
    [
        (None, GENERAL_CHAT_EXPECTED_EXCLUDED_SOURCES),
        ("research-copilot", INVOCATION_CONVERSATION_SOURCES),
    ],
)
async def test_list_threads_hides_research_copilot_only_from_unscoped_requests(
    monkeypatch: pytest.MonkeyPatch,
    agent_id: str | None,
    expected_sources: tuple[str, ...],
):
    repository = _ConversationRepository(object())
    monkeypatch.setattr(conversation_service, "ConversationRepository", lambda _db: repository)

    result = await conversation_service.list_threads_view(
        agent_slug=agent_id,
        db=object(),
        current_uid="user-a",
    )

    assert result == []
    assert repository.calls[0]["exclude_sources"] == expected_sources


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("agent_id", "expected_sources"),
    [
        (None, GENERAL_CHAT_EXPECTED_EXCLUDED_SOURCES),
        ("research-copilot", INVOCATION_CONVERSATION_SOURCES),
    ],
)
async def test_search_threads_hides_research_copilot_only_from_unscoped_requests(
    monkeypatch: pytest.MonkeyPatch,
    agent_id: str | None,
    expected_sources: tuple[str, ...],
):
    repository = _ConversationRepository(object())
    monkeypatch.setattr(conversation_service, "ConversationRepository", lambda _db: repository)

    result = await conversation_service.search_threads_view(
        query="research",
        agent_id=agent_id,
        db=object(),
        current_uid="user-a",
    )

    assert result == {"items": [], "has_more": False, "limit": 20, "offset": 0}
    assert repository.calls[0]["exclude_sources"] == expected_sources
