from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from yuxi.agents.buildin.chatbot.context import ChatBotContext

agent_router_module = importlib.import_module("server.routers.agent_router")


def _copilot_payload(**values):
    defaults = {
        "query": "总结当前项目",
        "agent_slug": "research-copilot",
        "thread_id": "thread-1",
        "meta": {"research_context": {"kb_id": "forged-kb"}},
        "tool_approval_mode": "always_trust",
    }
    defaults.update(values)
    return agent_router_module.AgentRunCreate(**defaults)


def test_agent_config_drops_private_research_runtime_fields(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        agent_router_module.agent_manager,
        "get_agent",
        lambda _backend_id: SimpleNamespace(context_schema=ChatBotContext),
    )

    result = agent_router_module._filter_agent_config_json(
        "ChatbotAgent",
        {
            "context": {
                "system_prompt": "custom",
                "tools": ["research_delete_project", "ask_user_question"],
                "research_context": {"kb_id": "forged-kb"},
                "runtime_agent_slug": "research-copilot",
                "research_tools_enabled": True,
            }
        },
        "user",
    )

    assert result == {
        "context": {
            "system_prompt": "custom",
            "tools": ["ask_user_question"],
        }
    }


@pytest.mark.asyncio
async def test_copilot_chat_revalidates_context_and_forces_default_approval(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}
    server_context = {"kb_id": "kb-1", "project_id": "project-1"}

    async def fake_prepare(**kwargs):
        captured["prepare"] = kwargs
        return {
            **kwargs["meta"],
            "source": "research_copilot",
            "research_context": server_context,
        }

    class FakeAgentRepository:
        def __init__(self, db):
            del db

        async def get_visible_by_slug(self, **kwargs):
            del kwargs
            return SimpleNamespace(backend_id="ChatbotAgent")

    async def fake_intake(**kwargs):
        captured["intake"] = kwargs
        return SimpleNamespace(
            request_id=kwargs["request_id"],
            status="queued",
            queue_policy="enqueue",
            queue_position=1,
            message_id=10,
            run_id=None,
            thread_id=kwargs["thread_id"],
        )

    async def fake_finalize(**kwargs):
        captured["finalize"] = kwargs

    monkeypatch.setattr(agent_router_module, "prepare_research_copilot_run_meta", fake_prepare)
    monkeypatch.setattr(agent_router_module, "AgentRepository", FakeAgentRepository)
    monkeypatch.setattr(agent_router_module.agent_manager, "get_agent", lambda _backend_id: object())
    monkeypatch.setattr(agent_router_module, "intake_request", fake_intake)
    monkeypatch.setattr(agent_router_module, "finalize_intake", fake_finalize)

    await agent_router_module.create_agent_run(
        payload=_copilot_payload(),
        current_user=SimpleNamespace(uid="user-1"),
        db=object(),
    )

    assert captured["prepare"]["meta"]["research_context"] == {"kb_id": "forged-kb"}
    intake = captured["intake"]
    assert intake["source"] == "research_copilot"
    assert intake["tool_approval_mode"] == "default"
    assert intake["meta"]["tool_approval_mode"] == "default"
    assert intake["meta"]["research_context"] == server_context


@pytest.mark.asyncio
async def test_copilot_resume_forces_default_over_parent_snapshot(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}
    server_context = {"kb_id": "kb-1", "project_id": "project-1"}

    async def fake_prepare(**kwargs):
        return {**kwargs["meta"], "research_context": server_context}

    async def fake_create_run(**kwargs):
        captured.update(kwargs)
        return {"run_id": "resume-run"}

    monkeypatch.setattr(agent_router_module, "prepare_research_copilot_run_meta", fake_prepare)
    monkeypatch.setattr(agent_router_module, "create_agent_run_view", fake_create_run)

    result = await agent_router_module.create_agent_run(
        payload=_copilot_payload(
            query=None,
            resume={"decisions": [{"type": "approve"}]},
            created_by_run_id="parent-run",
        ),
        current_user=SimpleNamespace(uid="user-1"),
        db=object(),
    )

    assert result == {"run_id": "resume-run"}
    assert captured["meta"]["research_context"] == server_context
    assert captured["tool_approval_mode"] == "default"
    assert captured["force_tool_approval_mode"] == "default"


@pytest.mark.asyncio
async def test_non_copilot_resume_keeps_requested_approval_mode(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}

    async def fake_prepare(**kwargs):
        return kwargs["meta"]

    async def fake_create_run(**kwargs):
        captured.update(kwargs)
        return {"run_id": "resume-run"}

    monkeypatch.setattr(agent_router_module, "prepare_research_copilot_run_meta", fake_prepare)
    monkeypatch.setattr(agent_router_module, "create_agent_run_view", fake_create_run)

    await agent_router_module.create_agent_run(
        payload=agent_router_module.AgentRunCreate(
            agent_slug="default-chatbot",
            thread_id="thread-1",
            resume={"answer": "yes"},
            tool_approval_mode="always_trust",
            created_by_run_id="parent-run",
        ),
        current_user=SimpleNamespace(uid="user-1"),
        db=object(),
    )

    assert captured["tool_approval_mode"] == "always_trust"
    assert captured["force_tool_approval_mode"] is None


@pytest.mark.asyncio
async def test_copilot_run_maps_invalid_thread_to_conflict(monkeypatch: pytest.MonkeyPatch):
    async def fake_prepare(**_kwargs):
        raise agent_router_module.ResearchCopilotError("invalid_copilot_thread", "会话无效")

    monkeypatch.setattr(agent_router_module, "prepare_research_copilot_run_meta", fake_prepare)

    with pytest.raises(HTTPException) as exc_info:
        await agent_router_module.create_agent_run(
            payload=_copilot_payload(),
            current_user=SimpleNamespace(uid="user-1"),
            db=object(),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == {"error": "invalid_copilot_thread", "message": "会话无效"}


@pytest.mark.asyncio
async def test_admin_cannot_update_research_copilot(monkeypatch: pytest.MonkeyPatch):
    class FakeAgentRepository:
        def __init__(self, db):
            del db

        async def get_visible_by_slug(self, **kwargs):
            del kwargs
            return SimpleNamespace(
                slug="research-copilot",
                backend_id="ChatbotAgent",
                created_by="system",
            )

        async def update(self, **_kwargs):
            raise AssertionError("protected Copilot must not be updated")

    monkeypatch.setattr(agent_router_module, "AgentRepository", FakeAgentRepository)

    with pytest.raises(HTTPException) as exc_info:
        await agent_router_module.update_agent(
            agent_id="research-copilot",
            payload=agent_router_module.AgentUpdate(name="tampered"),
            current_user=SimpleNamespace(uid="admin-1", role="admin"),
            db=object(),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "内置智能体不能编辑"


@pytest.mark.asyncio
async def test_admin_can_still_update_default_agent(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}
    agent = SimpleNamespace(
        slug="default-chatbot",
        backend_id="ChatbotAgent",
        created_by="system",
    )

    class FakeAgentRepository:
        def __init__(self, db):
            del db

        async def get_visible_by_slug(self, **kwargs):
            del kwargs
            return agent

        async def update(self, item, **kwargs):
            captured["item"] = item
            captured["kwargs"] = kwargs
            return item

    async def fake_serialize(_repo, item, _user, **_kwargs):
        return {"agent_id": item.slug}

    monkeypatch.setattr(agent_router_module, "AgentRepository", FakeAgentRepository)
    monkeypatch.setattr(agent_router_module, "_serialize_agent", fake_serialize)

    result = await agent_router_module.update_agent(
        agent_id="default-chatbot",
        payload=agent_router_module.AgentUpdate(name="Updated default"),
        current_user=SimpleNamespace(uid="admin-1", role="admin"),
        db=object(),
    )

    assert result == {"agent": {"agent_id": "default-chatbot"}}
    assert captured["item"] is agent
    assert captured["kwargs"]["name"] == "Updated default"


@pytest.mark.asyncio
async def test_admin_cannot_delete_research_copilot(monkeypatch: pytest.MonkeyPatch):
    class FakeAgentRepository:
        def __init__(self, db):
            del db

        async def get_visible_by_slug(self, **kwargs):
            del kwargs
            return SimpleNamespace(slug="research-copilot", created_by="system")

        async def delete(self, **_kwargs):
            raise AssertionError("protected Copilot must not be deleted")

    monkeypatch.setattr(agent_router_module, "AgentRepository", FakeAgentRepository)

    with pytest.raises(HTTPException) as exc_info:
        await agent_router_module.delete_agent(
            agent_id="research-copilot",
            current_user=SimpleNamespace(uid="admin-1", role="admin"),
            db=object(),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "内置智能体不能删除"


@pytest.mark.asyncio
async def test_research_copilot_cannot_be_set_as_default(monkeypatch: pytest.MonkeyPatch):
    class FakeAgentRepository:
        def __init__(self, db):
            del db

        async def get_by_slug(self, _slug):
            return SimpleNamespace(slug="research-copilot", is_subagent=False)

        async def set_default(self, **_kwargs):
            raise ValueError("默认智能体已固定为内置智能助手")

    monkeypatch.setattr(agent_router_module, "AgentRepository", FakeAgentRepository)

    with pytest.raises(HTTPException) as exc_info:
        await agent_router_module.set_agent_default(
            agent_id="research-copilot",
            current_user=SimpleNamespace(uid="admin-1", role="admin"),
            db=object(),
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "默认智能体已固定为内置智能助手"
