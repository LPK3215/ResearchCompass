from __future__ import annotations

import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest

from yuxi import config
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Department, User
from yuxi.utils.auth_utils import AuthUtils

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e, pytest.mark.slow]

POLL_INTERVAL_SECONDS = float(os.getenv("E2E_RUN_POLL_INTERVAL_SECONDS", "2"))
RUN_TIMEOUT_SECONDS = int(os.getenv("E2E_RUN_TIMEOUT_SECONDS", "240"))
EXPECTED_OUTPUT = "RESEARCH_COPILOT_CONTEXT_E2E_OK"


@asynccontextmanager
async def _temporary_superadmin(client: httpx.AsyncClient):
    suffix = uuid.uuid4().hex[:12]
    uid = f"e2e_copilot_{suffix}"
    password = f"Pw!{uuid.uuid4().hex}"
    user_id: int | None = None
    department_id: int | None = None

    pg_manager.initialize()
    try:
        async with pg_manager.get_async_session_context() as session:
            department = Department(
                name=f"e2e_copilot_{suffix}",
                description="Research Copilot E2E department",
            )
            session.add(department)
            await session.flush()
            department_id = department.id

            user = User(
                username=uid,
                uid=uid,
                password_hash=AuthUtils.hash_password(password),
                role="superadmin",
                department_id=department_id,
            )
            session.add(user)
            await session.flush()
            user_id = user.id

        response = await client.post("/api/auth/token", data={"username": uid, "password": password})
        assert response.status_code == 200, response.text
        access_token = response.json().get("access_token")
        assert access_token, response.text
        yield {"headers": {"Authorization": f"Bearer {access_token}"}, "uid": uid}
    finally:
        if user_id is not None:
            async with pg_manager.get_async_session_context() as session:
                user = await session.get(User, user_id)
                if user is not None:
                    await session.delete(user)
                department = await session.get(Department, department_id)
                if department is not None:
                    await session.delete(department)


async def _create_knowledge_database(client: httpx.AsyncClient, headers: dict[str, str]) -> str:
    response = await client.post(
        "/api/knowledge/databases",
        json={
            "database_name": f"e2e_copilot_{uuid.uuid4().hex[:12]}",
            "description": "Research Copilot E2E knowledge base",
            "embedding_model_spec": config.embed_model,
            "kb_type": "milvus",
            "additional_params": {},
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    kb_id = response.json().get("kb_id")
    assert kb_id, response.text
    return str(kb_id)


async def _wait_for_result(client: httpx.AsyncClient, headers: dict[str, str], run_id: str) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + RUN_TIMEOUT_SECONDS
    last_payload: dict[str, Any] | None = None

    while asyncio.get_running_loop().time() < deadline:
        response = await client.get(f"/api/agent/runs/{run_id}/result", headers=headers)
        assert response.status_code == 200, response.text
        last_payload = response.json()
        if last_payload.get("status") in {"completed", "failed", "cancelled", "interrupted"}:
            return last_payload
        await asyncio.sleep(POLL_INTERVAL_SECONDS)

    pytest.fail("Research Copilot run timed out: " + json.dumps(last_payload or {}, ensure_ascii=False))


async def test_research_copilot_restores_thread_and_reads_trusted_context(
    e2e_client: httpx.AsyncClient,
):
    kb_id: str | None = None
    thread_id: str | None = None
    run_id: str | None = None
    run_completed = False

    async with _temporary_superadmin(e2e_client) as account:
        headers = account["headers"]
        try:
            kb_id = await _create_knowledge_database(e2e_client, headers)

            first_response = await e2e_client.post(
                "/api/research/copilot/thread",
                json={"kb_id": kb_id, "surface": "projects"},
                headers=headers,
            )
            assert first_response.status_code == 200, first_response.text
            first = first_response.json()
            thread_id = str(first["thread"]["id"])
            assert first["agent_id"] == "research-copilot"
            assert first["research_context"]["kb_id"] == kb_id
            assert first["research_context"]["surface"] == "projects"

            restored_response = await e2e_client.post(
                "/api/research/copilot/thread",
                json={"kb_id": kb_id, "surface": "synthesis"},
                headers=headers,
            )
            assert restored_response.status_code == 200, restored_response.text
            restored = restored_response.json()
            assert restored["thread"]["id"] == thread_id
            assert restored["thread"]["metadata"]["source"] == "research_copilot"
            assert restored["thread"]["metadata"]["research_context"]["surface"] == "synthesis"

            default_response = await e2e_client.get("/api/agent/default", headers=headers)
            assert default_response.status_code == 200, default_response.text
            default_context = ((default_response.json().get("agent") or {}).get("config_json") or {}).get(
                "context"
            ) or {}

            request_id = f"research-copilot-e2e-{uuid.uuid4()}"
            run_payload: dict[str, Any] = {
                "query": (
                    "这是端到端校验。必须先且只调用 research_get_context 工具一次读取可信研究上下文；"
                    f"工具成功后只回复 {EXPECTED_OUTPUT}，不要调用其他工具。"
                ),
                "agent_slug": "research-copilot",
                "thread_id": thread_id,
                "meta": {
                    "request_id": request_id,
                    "source": "forged-source",
                    "research_context": {"kb_id": "forged-kb-id", "surface": "graph"},
                },
            }
            if default_context.get("model"):
                run_payload["model_spec"] = default_context["model"]

            create_response = await e2e_client.post(
                "/api/agent/runs",
                json=run_payload,
                headers=headers,
            )
            assert create_response.status_code == 200, create_response.text
            created = create_response.json()
            run_id = str(created["run_id"])
            assert created["request_id"] == request_id
            assert created["stream_url"] == f"/api/agent/runs/{run_id}/events"

            result = await _wait_for_result(e2e_client, headers, run_id)
            assert result["status"] == "completed", result
            assert result["request_id"] == request_id
            assert result["thread_id"] == thread_id
            assert EXPECTED_OUTPUT in str(result.get("output") or ""), result
            run_completed = True

            run_response = await e2e_client.get(f"/api/agent/runs/{run_id}", headers=headers)
            assert run_response.status_code == 200, run_response.text
            assert (run_response.json().get("run") or {}).get("status") == "completed"

            history_response = await e2e_client.get(f"/api/chat/thread/{thread_id}/history", headers=headers)
            assert history_response.status_code == 200, history_response.text
            history = history_response.json().get("history") or []

            input_messages = [
                item for item in history if item.get("type") == "human" and item.get("request_id") == request_id
            ]
            assert input_messages, history
            input_message = input_messages[0]
            input_metadata = input_message.get("extra_metadata") or {}
            assert input_metadata["source"] == "research_copilot"
            assert input_metadata["research_context"]["kb_id"] == kb_id
            assert input_metadata["research_context"]["surface"] == "synthesis"
            assert "forged-kb-id" not in json.dumps(input_metadata, ensure_ascii=False)

            context_tool_calls = [
                tool_call
                for item in history
                for tool_call in item.get("tool_calls") or []
                if tool_call.get("name") == "research_get_context"
            ]
            successful_calls = [tool_call for tool_call in context_tool_calls if tool_call.get("status") == "success"]
            assert successful_calls, context_tool_calls
            successful_call = successful_calls[0]
            tool_output = str((successful_call.get("tool_call_result") or {}).get("content") or "")
            assert kb_id in tool_output, successful_call
            assert "synthesis" in tool_output, successful_call
            assert "forged-kb-id" not in tool_output, successful_call
        finally:
            if run_id and not run_completed:
                cancel_response = await e2e_client.post(f"/api/agent/runs/{run_id}/cancel", headers=headers)
                assert cancel_response.status_code < 500, cancel_response.text
            if thread_id:
                delete_thread_response = await e2e_client.delete(f"/api/chat/thread/{thread_id}", headers=headers)
                assert delete_thread_response.status_code in {200, 404}, delete_thread_response.text
            if kb_id:
                delete_kb_response = await e2e_client.delete(f"/api/knowledge/databases/{kb_id}", headers=headers)
                assert delete_kb_response.status_code in {200, 404}, delete_kb_response.text
