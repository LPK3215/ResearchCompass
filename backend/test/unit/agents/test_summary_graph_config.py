from __future__ import annotations

from types import SimpleNamespace

import pytest

from yuxi.agents.buildin.chatbot import graph as chatbot_graph
from yuxi.agents.buildin.subagent import graph as subagent_graph


def _context(summary_threshold: int = 123) -> SimpleNamespace:
    return SimpleNamespace(
        model="",
        summary_threshold=summary_threshold,
        summary_keep_messages=7,
        summary_prompt="SUMMARY {messages}",
        summary_tool_result_token_limit=300,
        summary_l2_trigger_ratio=0.75,
        tool_token_limit=3,
        model_retry_times=1,
    )


def _patch_common_graph_deps(monkeypatch: pytest.MonkeyPatch, graph_module, captured: dict) -> None:
    monkeypatch.setattr(graph_module, "load_chat_model", lambda fully_specified_name: object())
    monkeypatch.setattr(graph_module, "create_agent_filesystem_middleware", lambda *_args, **_kwargs: object())

    def create_summary_middleware(**kwargs):
        captured["summary_kwargs"] = kwargs
        return object()

    monkeypatch.setattr(graph_module, "create_summary_middleware", create_summary_middleware)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_chatbot_summary_trim_limit_matches_summary_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}
    _patch_common_graph_deps(monkeypatch, chatbot_graph, captured)

    async def no_subagent_middleware(_context):
        return None

    monkeypatch.setattr(chatbot_graph, "create_subagent_task_middleware", no_subagent_middleware)

    middlewares = await chatbot_graph._build_middlewares(_context(summary_threshold=123))

    assert captured["summary_kwargs"]["trigger"] == ("tokens", 123 * 1024)
    assert captured["summary_kwargs"]["trim_tokens_to_summarize"] == 123 * 1024
    assert captured["summary_kwargs"]["l1_l2_trigger_ratio"] == 0.75
    middleware_names = [type(middleware).__name__ for middleware in middlewares]
    assert middleware_names.index("ModelRetryMiddleware") < middleware_names.index("ImageInputCompatibilityMiddleware")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_chatbot_research_copilot_forces_default_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}
    context = _context()
    context.runtime_agent_slug = "research-copilot"
    context.tool_approval_mode = "always_trust"
    _patch_common_graph_deps(monkeypatch, chatbot_graph, captured)

    async def no_subagent_middleware(_context):
        return None

    def capture_approval_mode(mode):
        captured["approval_mode"] = mode
        return None

    monkeypatch.setattr(chatbot_graph, "create_subagent_task_middleware", no_subagent_middleware)
    monkeypatch.setattr(chatbot_graph, "create_tool_approval_middleware", capture_approval_mode)

    await chatbot_graph._build_middlewares(context)

    assert captured["approval_mode"] == "default"


def test_research_tools_require_copilot_agent_and_trusted_context_marker() -> None:
    assert chatbot_graph._research_tools_are_enabled(
        SimpleNamespace(runtime_agent_slug="research-copilot", research_tools_enabled=True)
    )
    assert not chatbot_graph._research_tools_are_enabled(
        SimpleNamespace(runtime_agent_slug="research-copilot", research_tools_enabled=False)
    )
    assert not chatbot_graph._research_tools_are_enabled(
        SimpleNamespace(runtime_agent_slug="custom-agent", research_tools_enabled=True)
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_subagent_summary_trim_limit_matches_summary_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}
    _patch_common_graph_deps(monkeypatch, subagent_graph, captured)

    middlewares = await subagent_graph._build_middlewares(_context(summary_threshold=64))

    assert captured["summary_kwargs"]["trigger"] == ("tokens", 64 * 1024)
    assert captured["summary_kwargs"]["trim_tokens_to_summarize"] == 64 * 1024
    assert captured["summary_kwargs"]["l1_l2_trigger_ratio"] == 0.75
    middleware_names = [type(middleware).__name__ for middleware in middlewares]
    assert middleware_names.index("ModelRetryMiddleware") < middleware_names.index("ImageInputCompatibilityMiddleware")
