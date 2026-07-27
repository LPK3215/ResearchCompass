from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.repositories.agent_repository import (
    AgentRepository,
    DEFAULT_AGENT_BACKEND_ID,
    DEFAULT_AGENT_SLUG,
    DEFAULT_SHARE_CONFIG,
    RESEARCH_COPILOT_AGENT_DESCRIPTION,
    RESEARCH_COPILOT_AGENT_NAME,
    RESEARCH_COPILOT_AGENT_SLUG,
    RESEARCH_COPILOT_SYSTEM_PROMPT,
    RESEARCH_COPILOT_TOOL_SLUGS,
    RESEARCH_EXPLORER_AGENT_SLUG,
    FACT_VERIFIER_AGENT_SLUG,
    is_builtin_agent,
)


class FakeDb:
    def __init__(self):
        self.added = None
        self.commit = AsyncMock()
        self.refresh = AsyncMock()

    def add(self, item):
        self.added = item


def _expected_config() -> dict:
    return {
        "context": {
            "system_prompt": RESEARCH_COPILOT_SYSTEM_PROMPT,
            "tools": list(RESEARCH_COPILOT_TOOL_SLUGS),
            "knowledges": [],
            "mcps": [],
            "skills": [],
            "subagents": [RESEARCH_EXPLORER_AGENT_SLUG, FACT_VERIFIER_AGENT_SLUG],
        }
    }


def test_research_copilot_is_builtin_without_becoming_the_default_agent():
    assert is_builtin_agent(SimpleNamespace(slug=DEFAULT_AGENT_SLUG)) is True
    assert is_builtin_agent(SimpleNamespace(slug=RESEARCH_COPILOT_AGENT_SLUG)) is True
    assert is_builtin_agent(SimpleNamespace(slug="custom-agent")) is False


@pytest.mark.asyncio
async def test_ensure_research_copilot_creates_canonical_agent(monkeypatch):
    db = FakeDb()
    repo = AgentRepository(db)

    async def get_by_slug(_slug):
        return None

    monkeypatch.setattr(repo, "get_by_slug", get_by_slug)

    agent = await repo.ensure_research_copilot_agent(created_by="system")

    assert agent is db.added
    assert agent.slug == RESEARCH_COPILOT_AGENT_SLUG
    assert agent.backend_id == DEFAULT_AGENT_BACKEND_ID
    assert agent.name == RESEARCH_COPILOT_AGENT_NAME
    assert agent.description == RESEARCH_COPILOT_AGENT_DESCRIPTION
    assert agent.config_json == _expected_config()
    assert agent.share_config == DEFAULT_SHARE_CONFIG
    assert agent.is_subagent is False
    assert agent.is_default is False
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(agent)


@pytest.mark.asyncio
async def test_ensure_research_copilot_replaces_preempted_configuration(monkeypatch):
    db = FakeDb()
    repo = AgentRepository(db)
    agent = SimpleNamespace(
        slug=RESEARCH_COPILOT_AGENT_SLUG,
        backend_id="SubAgentBackend",
        name="Injected agent",
        description="Untrusted description",
        config_json={"context": {"tools": ["execute"]}},
        share_config={"access_level": "user", "department_ids": [], "user_uids": ["attacker"]},
        is_default=True,
        is_subagent=True,
        updated_by=None,
        updated_at=None,
    )

    async def get_by_slug(_slug):
        return agent

    monkeypatch.setattr(repo, "get_by_slug", get_by_slug)

    result = await repo.ensure_research_copilot_agent(created_by="system")

    assert result is agent
    assert agent.backend_id == DEFAULT_AGENT_BACKEND_ID
    assert agent.name == RESEARCH_COPILOT_AGENT_NAME
    assert agent.description == RESEARCH_COPILOT_AGENT_DESCRIPTION
    assert agent.config_json == _expected_config()
    assert agent.share_config == DEFAULT_SHARE_CONFIG
    assert agent.is_default is False
    assert agent.is_subagent is False
    assert agent.updated_by == "system"
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(agent)


@pytest.mark.asyncio
async def test_research_copilot_cannot_be_set_as_default():
    repo = AgentRepository(FakeDb())
    agent = SimpleNamespace(
        slug=RESEARCH_COPILOT_AGENT_SLUG,
        is_subagent=False,
        share_config=DEFAULT_SHARE_CONFIG.copy(),
    )

    with pytest.raises(ValueError, match="默认智能体已固定为内置智能助手"):
        await repo.set_default(agent=agent)
