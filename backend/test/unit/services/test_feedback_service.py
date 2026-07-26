from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from yuxi.services import feedback_service as svc


class _FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _FakeSession:
    def __init__(self, results):
        self.results = list(results)
        self.added = []
        self.committed = False
        self.rolled_back = False

    async def execute(self, _query):
        value = self.results.pop(0)
        if isinstance(value, Exception):
            raise value
        return _FakeResult(value)

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        self.committed = True

    async def refresh(self, item):
        item.id = 9
        item.created_at = datetime(2026, 1, 2, 3, 4, 5)

    async def rollback(self):
        self.rolled_back = True


@pytest.mark.asyncio
async def test_submit_message_feedback_syncs_langfuse_score(monkeypatch: pytest.MonkeyPatch):
    message = SimpleNamespace(
        id=3,
        conversation_id=7,
        extra_metadata={"langfuse_trace_id": "trace-1"},
    )
    conversation = SimpleNamespace(id=7, uid="user-1")
    db = _FakeSession([message, conversation, None])
    calls = []

    monkeypatch.setattr(svc, "submit_user_feedback_score", lambda **kwargs: calls.append(kwargs) or True)

    result = await svc.submit_message_feedback_view(
        message_id=3,
        rating="like",
        reason=None,
        db=db,
        current_uid="user-1",
    )

    assert result == {
        "id": 9,
        "message_id": 3,
        "rating": "like",
        "reason": None,
        "created_at": "2026-01-02T03:04:05",
    }
    assert db.committed is True
    assert db.rolled_back is False
    assert calls == [
        {
            "trace_id": "trace-1",
            "feedback_id": 9,
            "message_id": 3,
            "conversation_id": 7,
            "uid": "user-1",
            "rating": "like",
            "reason": None,
        }
    ]


@pytest.mark.asyncio
async def test_submit_message_feedback_skips_langfuse_without_trace_id(monkeypatch: pytest.MonkeyPatch):
    message = SimpleNamespace(id=3, conversation_id=7, extra_metadata={})
    conversation = SimpleNamespace(id=7, uid="user-1")
    db = _FakeSession([message, conversation, None])
    calls = []

    monkeypatch.setattr(svc, "submit_user_feedback_score", lambda **kwargs: calls.append(kwargs) or True)

    result = await svc.submit_message_feedback_view(
        message_id=3,
        rating="dislike",
        reason="不相关",
        db=db,
        current_uid="user-1",
    )

    assert result["rating"] == "dislike"
    assert result["reason"] == "不相关"
    assert calls == []


@pytest.mark.asyncio
async def test_get_message_feedback_requires_conversation_ownership():
    message = SimpleNamespace(id=3, conversation_id=7, extra_metadata={})
    foreign_conversation = SimpleNamespace(id=7, uid="user-2")
    db = _FakeSession([message, foreign_conversation])

    with pytest.raises(HTTPException) as exc_info:
        await svc.get_message_feedback_view(message_id=3, db=db, current_uid="user-1")

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Access denied"


@pytest.mark.asyncio
async def test_get_message_feedback_sanitizes_database_failure(monkeypatch: pytest.MonkeyPatch):
    secret = "Authorization=secret-api-key provider-body=<private>"
    messages: list[str] = []
    db = _FakeSession([RuntimeError(secret)])
    monkeypatch.setattr(svc.logger, "error", messages.append)

    with pytest.raises(HTTPException) as exc_info:
        await svc.get_message_feedback_view(message_id=3, db=db, current_uid="user-1")

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Failed to get feedback"
    assert secret not in " ".join(messages)
