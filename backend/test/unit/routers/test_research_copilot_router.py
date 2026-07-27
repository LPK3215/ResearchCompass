from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.utils.auth_middleware import get_db, get_required_user

research_router_module = importlib.import_module("server.routers.research_router")


def _build_client() -> tuple[TestClient, object, SimpleNamespace]:
    app = FastAPI()
    app.include_router(research_router_module.research, prefix="/api")
    db = object()
    user = SimpleNamespace(uid="user-1", role="user", department_id=1)

    async def fake_db():
        return db

    async def fake_user():
        return user

    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_required_user] = fake_user
    return TestClient(app), db, user


def test_create_research_copilot_thread_forwards_validated_scope(monkeypatch):
    captured: dict[str, object] = {}

    async def ensure_thread(**kwargs):
        captured.update(kwargs)
        return {
            "agent_id": "research-copilot",
            "thread": {"id": "thread-1"},
            "research_context": {"kb_id": "kb-1", "scope_key": "kb-1:library"},
        }

    monkeypatch.setattr(research_router_module, "ensure_research_copilot_thread", ensure_thread)
    client, db, user = _build_client()

    response = client.post(
        "/api/research/copilot/thread",
        json={
            "kb_id": "kb-1",
            "surface": "library",
            "selection": {"type": "paper", "id": "paper-1", "title": "Source paper"},
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["thread"]["id"] == "thread-1"
    assert captured == {
        "payload": {
            "kb_id": "kb-1",
            "project_id": None,
            "surface": "library",
            "selection": {"type": "paper", "id": "paper-1", "title": "Source paper"},
        },
        "current_user": user,
        "db": db,
    }


@pytest.mark.parametrize(
    ("error_type", "expected_status"),
    [
        ("forbidden", 403),
        ("knowledge_base_not_found", 404),
        ("project_not_found", 404),
        ("thread_not_found", 404),
        ("invalid_copilot_thread", 409),
        ("project_scope_mismatch", 409),
        ("thread_update_failed", 409),
        ("invalid_research_context", 422),
    ],
)
def test_create_research_copilot_thread_maps_domain_errors(monkeypatch, error_type, expected_status):
    async def fail(**_kwargs):
        raise research_router_module.ResearchCopilotError(error_type, "copilot error")

    monkeypatch.setattr(research_router_module, "ensure_research_copilot_thread", fail)
    client, _, _ = _build_client()

    response = client.post(
        "/api/research/copilot/thread",
        json={"kb_id": "kb-1", "surface": "projects"},
    )

    assert response.status_code == expected_status
    assert response.json()["detail"] == {"error": error_type, "message": "copilot error"}
