import pytest

from yuxi.agents.tool_approval import (
    SENSITIVE_BACKEND_TOOLS,
    TOOL_APPROVAL_INTERRUPT_ON,
    create_tool_approval_middleware,
    normalize_tool_approval_mode,
)


def test_default_mode_builds_sensitive_tool_approval_middleware():
    middleware = create_tool_approval_middleware("default")

    assert middleware.interrupt_on == TOOL_APPROVAL_INTERRUPT_ON
    assert set(middleware.interrupt_on) == SENSITIVE_BACKEND_TOOLS
    assert all(config["allowed_decisions"] == ["approve", "reject"] for config in middleware.interrupt_on.values())


def test_always_trust_mode_does_not_build_approval_middleware():
    assert create_tool_approval_middleware("always_trust") is None


def test_sensitive_research_tools_require_approval():
    expected = {
        "research_set_project_status",
        "research_delete_project",
        "research_delete_milestone",
        "research_delete_task",
        "research_add_project_assets",
        "research_remove_project_asset",
        "research_unlink_asset_from_plan",
    }

    assert expected <= SENSITIVE_BACKEND_TOOLS
    assert "research_regenerate_synthesis" not in SENSITIVE_BACKEND_TOOLS


def test_unknown_tool_approval_mode_is_rejected():
    with pytest.raises(ValueError, match="不支持的 tool_approval_mode"):
        normalize_tool_approval_mode("unknown")
