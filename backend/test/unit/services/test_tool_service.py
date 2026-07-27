from __future__ import annotations

from types import SimpleNamespace

import pytest

from yuxi.agents.toolkits import service as tool_service


def test_get_tool_metadata_includes_config_guide(monkeypatch):
    tool_service._metadata_cache.clear()
    fake_tool = SimpleNamespace(
        name="demo_tool",
        description="demo description",
        metadata={},
        args_schema=None,
    )
    fake_extra = SimpleNamespace(
        category="buildin",
        tags=["demo"],
        display_name="演示工具",
        config_guide="请先配置 DEMO_API_KEY",
    )

    monkeypatch.setattr(
        "yuxi.agents.toolkits.registry.get_all_tool_instances",
        lambda: [fake_tool],
    )
    monkeypatch.setattr(
        "yuxi.agents.toolkits.registry.get_all_extra_metadata",
        lambda: {"demo_tool": fake_extra},
    )

    result = tool_service.get_tool_metadata()

    assert result == [
        {
            "slug": "demo_tool",
            "name": "演示工具",
            "description": "demo description",
            "metadata": {},
            "args": [],
            "category": "buildin",
            "tags": ["demo"],
            "config_guide": "请先配置 DEMO_API_KEY",
        }
    ]

    tool_service._metadata_cache.clear()


@pytest.mark.asyncio
async def test_research_tools_require_explicit_trusted_resolution(monkeypatch: pytest.MonkeyPatch):
    from yuxi.agents.toolkits.research import tools as research_tools

    context = SimpleNamespace(
        tools=["research_delete_project"],
        mcps=[],
        skills=[],
    )
    monkeypatch.setattr(
        "yuxi.agents.middlewares.skills.resolve_skill_gated_tools",
        lambda _context: [research_tools.research_delete_project],
    )

    generic_tools = await tool_service.resolve_configured_runtime_tools(context)
    trusted_tools = await tool_service.resolve_configured_runtime_tools(
        context,
        include_research_tools=True,
    )

    assert generic_tools == []
    assert [tool.name for tool in trusted_tools] == ["research_delete_project"]
