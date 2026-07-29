from __future__ import annotations

from types import SimpleNamespace

import pytest

from yuxi.agents.buildin.subagent import graph as subagent_graph


class _Request:
    def __init__(self, tools):
        self.tools = tools

    def override(self, **kwargs):
        return _Request(kwargs.get("tools", self.tools))


def _tool_call_request(tool_name: str):
    return SimpleNamespace(tool_call={"name": tool_name, "args": {}, "id": "forged-tool-call"})


def test_filter_disabled_tools_keeps_allowed_tools_order():
    tools = [
        SimpleNamespace(name="search"),
        SimpleNamespace(name="present_artifacts"),
        {"name": "ask_user_question"},
        SimpleNamespace(name="install_skill"),
        SimpleNamespace(name="calculator"),
    ]

    filtered = subagent_graph._filter_disabled_tools(tools, subagent_graph._disabled_tools_for("default"))

    assert [subagent_graph._tool_name(tool) for tool in filtered] == ["search", "calculator"]


def test_filter_disabled_tools_removes_sensitive_backend_tools_only_in_default_mode():
    tools = [
        SimpleNamespace(name="read_file"),
        SimpleNamespace(name="write_file"),
        SimpleNamespace(name="edit_file"),
        SimpleNamespace(name="execute"),
    ]

    default_mode_filtered = subagent_graph._filter_disabled_tools(tools, subagent_graph._disabled_tools_for("default"))
    assert [subagent_graph._tool_name(tool) for tool in default_mode_filtered] == ["read_file"]

    always_trust_filtered = subagent_graph._filter_disabled_tools(
        tools, subagent_graph._disabled_tools_for("always_trust")
    )
    assert [subagent_graph._tool_name(tool) for tool in always_trust_filtered] == [
        "read_file",
        "write_file",
        "edit_file",
        "execute",
    ]


def test_subagent_tool_filter_middleware_filters_before_handler():
    middleware = subagent_graph._SubAgentToolFilterMiddleware()
    seen = {}

    def handler(request):
        seen["tools"] = request.tools
        return "ok"

    result = middleware.wrap_model_call(
        _Request(
            [
                SimpleNamespace(name="present_artifacts"),
                SimpleNamespace(name="allowed_tool"),
            ]
        ),
        handler,
    )

    assert result == "ok"
    assert [tool.name for tool in seen["tools"]] == ["allowed_tool"]


@pytest.mark.asyncio
async def test_subagent_tool_filter_middleware_filters_async_before_handler():
    middleware = subagent_graph._SubAgentToolFilterMiddleware()
    seen = {}

    async def handler(request):
        seen["tools"] = request.tools
        return "ok"

    result = await middleware.awrap_model_call(
        _Request(
            [
                {"name": "ask_user_question"},
                SimpleNamespace(name="allowed_tool"),
            ]
        ),
        handler,
    )

    assert result == "ok"
    assert [subagent_graph._tool_name(tool) for tool in seen["tools"]] == ["allowed_tool"]


@pytest.mark.parametrize("tool_name", ["execute", "write_file", "edit_file", "install_skill"])
def test_subagent_tool_filter_rejects_forged_disabled_tool_call_before_execution(tool_name):
    middleware = subagent_graph._SubAgentToolFilterMiddleware("default")
    executed = False

    def handler(_request):
        nonlocal executed
        executed = True
        return "executed"

    with pytest.raises(PermissionError, match=tool_name):
        middleware.wrap_tool_call(_tool_call_request(tool_name), handler)

    assert executed is False


@pytest.mark.parametrize("tool_name", ["execute", "write_file", "edit_file", "install_skill"])
@pytest.mark.asyncio
async def test_subagent_tool_filter_rejects_forged_disabled_tool_call_async_before_execution(tool_name):
    middleware = subagent_graph._SubAgentToolFilterMiddleware("default")
    executed = False

    async def handler(_request):
        nonlocal executed
        executed = True
        return "executed"

    with pytest.raises(PermissionError, match=tool_name):
        await middleware.awrap_tool_call(_tool_call_request(tool_name), handler)

    assert executed is False


@pytest.mark.parametrize("tool_name", ["execute", "write_file", "edit_file"])
def test_subagent_tool_filter_allows_sensitive_tool_call_in_always_trust_mode(tool_name):
    middleware = subagent_graph._SubAgentToolFilterMiddleware("always_trust")

    result = middleware.wrap_tool_call(_tool_call_request(tool_name), lambda _request: "executed")

    assert result == "executed"


@pytest.mark.parametrize("tool_name", ["execute", "write_file", "edit_file"])
@pytest.mark.asyncio
async def test_subagent_tool_filter_allows_sensitive_tool_call_async_in_always_trust_mode(tool_name):
    middleware = subagent_graph._SubAgentToolFilterMiddleware("always_trust")

    async def handler(_request):
        return "executed"

    result = await middleware.awrap_tool_call(_tool_call_request(tool_name), handler)

    assert result == "executed"


def test_subagent_tool_filter_rejects_install_skill_in_always_trust_mode():
    middleware = subagent_graph._SubAgentToolFilterMiddleware("always_trust")
    executed = False

    def handler(_request):
        nonlocal executed
        executed = True
        return "executed"

    with pytest.raises(PermissionError, match="install_skill"):
        middleware.wrap_tool_call(_tool_call_request("install_skill"), handler)

    assert executed is False


@pytest.mark.asyncio
async def test_subagent_tool_filter_rejects_install_skill_async_in_always_trust_mode():
    middleware = subagent_graph._SubAgentToolFilterMiddleware("always_trust")
    executed = False

    async def handler(_request):
        nonlocal executed
        executed = True
        return "executed"

    with pytest.raises(PermissionError, match="install_skill"):
        await middleware.awrap_tool_call(_tool_call_request("install_skill"), handler)

    assert executed is False


@pytest.mark.asyncio
async def test_subagent_get_info_hides_disabled_tool_options(monkeypatch):
    async def get_info(_self, **_kwargs):
        return {
            "metadata": {},
            "configurable_items": {
                "tools": {
                    "options": [
                        {"key": "present_artifacts", "name": "展示交付物"},
                        {"key": "allowed_tool", "name": "Allowed"},
                        {"key": "ask_user_question", "name": "向用户提问"},
                        {"key": "install_skill", "name": "安装技能"},
                    ]
                }
            },
        }

    monkeypatch.setattr(subagent_graph.BaseAgent, "get_info", get_info)

    info = await subagent_graph.SubAgentBackend().get_info()

    assert [option["key"] for option in info["configurable_items"]["tools"]["options"]] == ["allowed_tool"]
