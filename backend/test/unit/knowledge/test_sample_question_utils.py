from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from yuxi.knowledge.utils import sample_question_utils as sq


class CapturedLogger:
    def __init__(self):
        self.messages: list[str] = []

    def info(self, message: str) -> None:
        self.messages.append(message)

    def error(self, message: str) -> None:
        self.messages.append(message)


def test_parse_sample_questions_content_strips_json_fence():
    questions = sq.parse_sample_questions_content('```json\n{"questions": ["什么是测试？"]}\n```')

    assert questions == ["什么是测试？"]


def test_parse_sample_questions_content_rejects_invalid_payload():
    with pytest.raises(ValueError, match="问题格式"):
        sq.parse_sample_questions_content('{"items": []}')


@pytest.mark.asyncio
async def test_generate_database_sample_questions_rejects_empty_files(monkeypatch):
    class FakeKnowledgeBase:
        async def get_database_info(self, kb_id: str, include_files: bool = False) -> dict:
            return {"name": "空知识库", "kb_type": "milvus", "files": {}}

    monkeypatch.setattr(sq, "knowledge_base", FakeKnowledgeBase())
    monkeypatch.setattr(
        sq.KnowledgeBaseFactory,
        "get_kb_class",
        lambda _kb_type: SimpleNamespace(supports_documents=True),
    )

    with pytest.raises(HTTPException) as exc_info:
        await sq.generate_database_sample_questions("kb_1")

    assert exc_info.value.status_code == 400
    assert "没有文件" in exc_info.value.detail


@pytest.mark.asyncio
async def test_generate_database_sample_questions_saves_and_returns_questions(monkeypatch):
    saved: dict = {}

    class FakeKnowledgeBase:
        async def get_database_info(self, kb_id: str, include_files: bool = False) -> dict:
            return {
                "name": "测试知识库",
                "kb_type": "milvus",
                "files": {"file_1": {"filename": "demo.md", "file_type": "md"}},
            }

    class FakeModel:
        async def call(self, messages, stream: bool = False):
            assert messages[0]["role"] == "system"
            assert "demo.md" in messages[1]["content"]
            return SimpleNamespace(content='{"questions": ["如何使用 demo？"]}')

    class FakeRepository:
        async def update(self, kb_id: str, data: dict) -> None:
            saved[kb_id] = data["sample_questions"]

        async def get_by_kb_id(self, kb_id: str):
            return SimpleNamespace(name="测试知识库", sample_questions=saved.get(kb_id))

    monkeypatch.setattr(sq, "knowledge_base", FakeKnowledgeBase())
    monkeypatch.setattr(
        sq.KnowledgeBaseFactory,
        "get_kb_class",
        lambda _kb_type: SimpleNamespace(supports_documents=True),
    )
    monkeypatch.setattr(sq, "select_model", lambda model_spec: FakeModel())
    monkeypatch.setattr(sq, "KnowledgeBaseRepository", lambda: FakeRepository())

    generated = await sq.generate_database_sample_questions("kb_1", count=1)
    stored = await sq.get_database_sample_questions("kb_1")

    assert generated["questions"] == ["如何使用 demo？"]
    assert generated["count"] == 1
    assert stored["questions"] == ["如何使用 demo？"]


@pytest.mark.asyncio
async def test_generate_database_sample_questions_maps_invalid_json(monkeypatch):
    secret = "Authorization=secret-api-key provider-body=<private>"
    captured_logger = CapturedLogger()

    class FakeKnowledgeBase:
        async def get_database_info(self, kb_id: str, include_files: bool = False) -> dict:
            return {
                "name": "测试知识库",
                "kb_type": "milvus",
                "files": {"file_1": {"filename": "demo.md", "file_type": "md"}},
            }

    class FakeModel:
        async def call(self, messages, stream: bool = False):
            return SimpleNamespace(content=secret)

    monkeypatch.setattr(sq, "knowledge_base", FakeKnowledgeBase())
    monkeypatch.setattr(
        sq.KnowledgeBaseFactory,
        "get_kb_class",
        lambda _kb_type: SimpleNamespace(supports_documents=True),
    )
    monkeypatch.setattr(sq, "select_model", lambda model_spec: FakeModel())
    monkeypatch.setattr(sq, "logger", captured_logger)

    with pytest.raises(HTTPException) as exc_info:
        await sq.generate_database_sample_questions("kb_1")

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "AI返回格式错误"
    assert secret not in " ".join(captured_logger.messages)


@pytest.mark.asyncio
async def test_generate_database_sample_questions_reports_persistence_failure(monkeypatch):
    secret = "Authorization=secret-api-key provider-body=<private>"
    captured_logger = CapturedLogger()

    class FakeKnowledgeBase:
        async def get_database_info(self, kb_id: str, include_files: bool = False) -> dict:
            return {
                "name": "测试知识库",
                "kb_type": "milvus",
                "files": {"file_1": {"filename": "demo.md", "file_type": "md"}},
            }

    class FakeModel:
        async def call(self, messages, stream: bool = False):
            return SimpleNamespace(content='{"questions": ["如何使用 demo？"]}')

    class FailingRepository:
        async def update(self, kb_id: str, data: dict) -> None:
            raise RuntimeError(secret)

    monkeypatch.setattr(sq, "knowledge_base", FakeKnowledgeBase())
    monkeypatch.setattr(
        sq.KnowledgeBaseFactory,
        "get_kb_class",
        lambda _kb_type: SimpleNamespace(supports_documents=True),
    )
    monkeypatch.setattr(sq, "select_model", lambda model_spec: FakeModel())
    monkeypatch.setattr(sq, "KnowledgeBaseRepository", FailingRepository)
    monkeypatch.setattr(sq, "logger", captured_logger)

    with pytest.raises(HTTPException) as exc_info:
        await sq.generate_database_sample_questions("kb_1", count=1)

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "示例问题保存失败"
    assert secret not in " ".join(captured_logger.messages)
