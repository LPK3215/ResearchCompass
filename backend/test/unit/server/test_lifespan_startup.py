from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from server.utils import lifespan as lifespan_module
from yuxi.repositories import agent_repository


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("lite_mode", "expects_copilot"),
    [("1", False), ("false", True)],
)
async def test_builtin_agent_startup_respects_lite_mode(
    monkeypatch: pytest.MonkeyPatch,
    lite_mode: str,
    expects_copilot: bool,
):
    calls: list[str] = []

    class FakeAgentRepository:
        def __init__(self, session):
            assert session is fake_session

        async def ensure_default_agent(self):
            calls.append("default")

        async def ensure_general_purpose_subagent(self):
            calls.append("general")

        async def ensure_web_search_subagent(self):
            calls.append("web_search")

        async def ensure_deep_research_agents(self):
            calls.append("deep_research")

        async def ensure_research_copilot_agent(self):
            calls.append("research_copilot")

    fake_session = object()

    @asynccontextmanager
    async def fake_session_context():
        yield fake_session

    monkeypatch.setenv("LITE_MODE", lite_mode)
    monkeypatch.setattr(agent_repository, "AgentRepository", FakeAgentRepository)
    monkeypatch.setattr(lifespan_module.pg_manager, "get_async_session_context", fake_session_context)

    await lifespan_module._ensure_builtin_agents()

    assert calls[:4] == ["default", "general", "web_search", "deep_research"]
    assert ("research_copilot" in calls) is expects_copilot


@pytest.mark.asyncio
async def test_research_task_recovery_is_skipped_in_lite_mode(monkeypatch):
    messages: list[str] = []
    monkeypatch.setenv("LITE_MODE", "1")
    monkeypatch.setattr(lifespan_module.logger, "info", messages.append)

    await lifespan_module._recover_research_tasks()

    assert messages == ["LITE_MODE enabled, skipping research task recovery"]
