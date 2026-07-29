"""
Integration tests for the task management router.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_task_routes_require_admin(test_client, standard_user):
    """Non-admin users should be blocked from accessing task APIs."""
    headers = standard_user["headers"]

    list_response = await test_client.get("/api/tasks", headers=headers)
    assert list_response.status_code == 403

    detail_response = await test_client.get("/api/tasks/some-task", headers=headers)
    assert detail_response.status_code == 403

    cancel_response = await test_client.post("/api/tasks/some-task/cancel", headers=headers)
    assert cancel_response.status_code == 403


async def test_admin_can_list_tasks(test_client, admin_headers):
    """Admin should receive a well-formed task list payload."""
    response = await test_client.get("/api/tasks", headers=admin_headers)
    assert response.status_code == 200, response.text

    payload = response.json()
    assert "tasks" in payload
    assert isinstance(payload["tasks"], list)
    assert "summary" in payload
    assert isinstance(payload["summary"], dict)


async def test_cancel_unknown_task_returns_client_error(test_client, admin_headers):
    """Cancelling a non-existent task should surface a 400 response."""
    response = await test_client.post("/api/tasks/not-real/cancel", headers=admin_headers)
    assert response.status_code == 400, response.text


async def test_enqueue_document_creates_task(
    test_client,
    admin_headers,
    knowledge_document,
):
    """Trigger knowledge ingestion to ensure a task record is materialised."""
    task_id = knowledge_document["task_id"]
    detail_response = await test_client.get(f"/api/tasks/{task_id}", headers=admin_headers)
    assert detail_response.status_code == 200, detail_response.text
    assert detail_response.json()["task"]["status"] == "success"

    list_response = await test_client.get("/api/tasks", headers=admin_headers)
    assert list_response.status_code == 200, list_response.text
    assert any(entry.get("id") == task_id for entry in list_response.json().get("tasks", []))


async def test_enqueue_document_rejects_empty_items(test_client, admin_headers, knowledge_database):
    response = await test_client.post(
        f"/api/knowledge/databases/{knowledge_database['kb_id']}/documents",
        json={"items": [], "params": {"content_type": "file"}},
        headers=admin_headers,
    )

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "items must not be empty"
