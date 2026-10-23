"""research_paper_translation_service 的对照输出与失败行为测试。"""

import asyncio
import json
import re
from types import SimpleNamespace

import pytest
from yuxi.services import research_paper_translation_service as translation


def _chunk(chunk_id: str, content: str, *, index: int, page: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        chunk_id=chunk_id,
        chunk_index=index,
        content=content,
        chunk_metadata={
            "section_title": "Method",
            "section_type": "methodology",
            "source_page_start": page,
            "source_page_end": page,
        },
    )


class FakePaperRepository:
    def __init__(self, file_id="file-1"):
        self.file_id = file_id

    async def get_by_paper_id(self, *, kb_id, paper_id):
        return SimpleNamespace(paper_id=paper_id, file_id=self.file_id)


class FakeKBRepository:
    def __init__(self, model_spec="kb:model"):
        self.model_spec = model_spec

    async def get_by_kb_id(self, kb_id):
        return SimpleNamespace(llm_model_spec=self.model_spec)


class FakeChunkRepository:
    def __init__(self, chunks):
        self.chunks = chunks

    async def list_academic_by_file_id(self, *, file_id, section_type=None, offset=0, limit=100):
        chunks = [
            chunk for chunk in self.chunks if not section_type or chunk.chunk_metadata["section_type"] == section_type
        ]
        return chunks[:limit], len(chunks)


class FakeModel:
    def __init__(self, content):
        self.content = content

    async def call(self, messages, stream=False):
        return SimpleNamespace(content=self.content)


@pytest.fixture
def allow_access(monkeypatch):
    async def _allow(*args, **kwargs):
        return None

    monkeypatch.setattr(translation, "_ensure_access", _allow)
    monkeypatch.setattr(translation, "KnowledgeBaseRepository", lambda: FakeKBRepository())


@pytest.mark.asyncio
async def test_translate_returns_ordered_paragraphs_with_pages(monkeypatch, allow_access):
    monkeypatch.setattr(translation, "AcademicPaperRepository", lambda: FakePaperRepository())
    monkeypatch.setattr(
        translation,
        "KnowledgeChunkRepository",
        lambda: FakeChunkRepository([_chunk("c1", "第一段", index=1, page=2), _chunk("c2", "第二段", index=2, page=3)]),
    )
    monkeypatch.setattr(
        translation,
        "select_model",
        lambda **kwargs: FakeModel(
            '{"paragraphs":[{"chunk_id":"c2","translation":"第二段译文"},{"chunk_id":"c1","translation":"第一段译文"}]}'
        ),
    )

    result = await translation.translate_paper(
        kb_id="kb-1",
        paper_id="paper-1",
        current_user=SimpleNamespace(uid="user-1"),
        model_spec="test:model",
    )

    assert result["paragraph_count"] == 2
    assert [item["chunk_id"] for item in result["paragraphs"]] == ["c1", "c2"]
    assert result["paragraphs"][0]["translation"] == "第一段译文"
    assert result["paragraphs"][0]["page_start"] == 2
    assert result["missing_chunk_ids"] == []


@pytest.mark.asyncio
async def test_translate_falls_back_to_knowledge_base_model(monkeypatch, allow_access):
    monkeypatch.setattr(translation, "AcademicPaperRepository", lambda: FakePaperRepository())
    monkeypatch.setattr(
        translation, "KnowledgeChunkRepository", lambda: FakeChunkRepository([_chunk("c1", "A", index=1)])
    )
    monkeypatch.setattr(
        translation, "select_model", lambda **kwargs: FakeModel('{"paragraphs":[{"chunk_id":"c1","translation":"甲"}]}')
    )

    result = await translation.translate_paper(
        kb_id="kb-1", paper_id="paper-1", current_user=SimpleNamespace(uid="user-1")
    )

    assert result["model"] == "kb:model"


@pytest.mark.asyncio
async def test_translate_reports_untranslated_chunks(monkeypatch, allow_access):
    monkeypatch.setattr(translation, "AcademicPaperRepository", lambda: FakePaperRepository())
    monkeypatch.setattr(
        translation,
        "KnowledgeChunkRepository",
        lambda: FakeChunkRepository([_chunk("c1", "A", index=1), _chunk("c2", "B", index=2)]),
    )
    monkeypatch.setattr(
        translation, "select_model", lambda **kwargs: FakeModel('{"paragraphs":[{"chunk_id":"c1","translation":"甲"}]}')
    )

    result = await translation.translate_paper(
        kb_id="kb-1", paper_id="paper-1", current_user=SimpleNamespace(uid="user-1"), model_spec="test:model"
    )

    assert result["paragraph_count"] == 1
    assert result["requested_count"] == 2
    assert result["missing_chunk_ids"] == ["c2"]


@pytest.mark.asyncio
async def test_translate_rejects_metadata_only_paper(monkeypatch, allow_access):
    monkeypatch.setattr(translation, "AcademicPaperRepository", lambda: FakePaperRepository(file_id=None))
    monkeypatch.setattr(translation, "KnowledgeChunkRepository", lambda: FakeChunkRepository([]))

    with pytest.raises(translation.ResearchPaperTranslationError) as exc_info:
        await translation.translate_paper(
            kb_id="kb-1", paper_id="paper-1", current_user=SimpleNamespace(uid="user-1"), model_spec="test:model"
        )

    assert exc_info.value.error_type == "paper_chunk_missing"


@pytest.mark.asyncio
async def test_translate_rejects_unparsable_model_output(monkeypatch, allow_access):
    monkeypatch.setattr(translation, "AcademicPaperRepository", lambda: FakePaperRepository())
    monkeypatch.setattr(
        translation, "KnowledgeChunkRepository", lambda: FakeChunkRepository([_chunk("c1", "A", index=1)])
    )
    monkeypatch.setattr(translation, "select_model", lambda **kwargs: FakeModel("not json"))

    with pytest.raises(translation.ResearchPaperTranslationError) as exc_info:
        await translation.translate_paper(
            kb_id="kb-1", paper_id="paper-1", current_user=SimpleNamespace(uid="user-1"), model_spec="test:model"
        )

    assert exc_info.value.error_type == "translation_invalid_json"


def test_build_translation_context_skips_empty_chunks():
    rows = [
        {"chunk_id": "c1", "chunk_index": 1, "source": "text", "page_start": 1, "section_title": None},
        {"chunk_id": "c2", "chunk_index": 2, "source": "  ", "page_start": 2, "section_title": None},
    ]

    context, selected = translation.build_translation_context(rows)

    assert "[chunk:c1]" in context
    assert "c2" not in context
    assert [row["chunk_id"] for row in selected] == ["c1"]


class BatchingModel:
    def __init__(self):
        self.batch_sizes: list[int] = []

    async def call(self, messages, stream=False):
        prompt = messages[-1]["content"]
        chunk_ids = re.findall(r"\[chunk:([^\]]+)\]", prompt)
        self.batch_sizes.append(len(chunk_ids))
        return SimpleNamespace(
            content=json.dumps(
                {
                    "paragraphs": [
                        {"chunk_id": chunk_id, "translation": f"译-{chunk_id}"} for chunk_id in chunk_ids
                    ]
                },
                ensure_ascii=False,
            )
        )


@pytest.mark.asyncio
async def test_translate_batches_large_requests(monkeypatch, allow_access):
    chunks = [_chunk(f"c{index}", f"第 {index} 段", index=index) for index in range(1, 6)]
    monkeypatch.setattr(translation, "AcademicPaperRepository", lambda: FakePaperRepository())
    monkeypatch.setattr(translation, "KnowledgeChunkRepository", lambda: FakeChunkRepository(chunks))
    model = BatchingModel()
    monkeypatch.setattr(translation, "select_model", lambda **kwargs: model)

    result = await translation.translate_paper(
        kb_id="kb-1", paper_id="paper-1", current_user=SimpleNamespace(uid="user-1"), limit=6, model_spec="test:model"
    )

    assert model.batch_sizes == [1, 1, 1, 1, 1]
    assert result["paragraph_count"] == 5
    assert [item["chunk_id"] for item in result["paragraphs"]] == ["c1", "c2", "c3", "c4", "c5"]


class HangingModel:
    async def call(self, messages, stream=False):
        await asyncio.sleep(5)
        return SimpleNamespace(content='{"paragraphs":[]}')


@pytest.mark.asyncio
async def test_translate_times_out_instead_of_hanging(monkeypatch, allow_access):
    monkeypatch.setattr(translation, "AcademicPaperRepository", lambda: FakePaperRepository())
    monkeypatch.setattr(
        translation, "KnowledgeChunkRepository", lambda: FakeChunkRepository([_chunk("c1", "A", index=1)])
    )
    monkeypatch.setattr(translation, "select_model", lambda **kwargs: HangingModel())
    monkeypatch.setattr(translation, "TRANSLATION_MODEL_TIMEOUT_SECONDS", 0.01)

    with pytest.raises(translation.ResearchPaperTranslationError) as exc_info:
        await translation.translate_paper(
            kb_id="kb-1", paper_id="paper-1", current_user=SimpleNamespace(uid="user-1"), model_spec="test:model"
        )

    assert exc_info.value.error_type == "translation_model_timeout"


@pytest.mark.asyncio
async def test_connection_failure_reports_unreachable_model(monkeypatch, allow_access):
    class UnreachableModel:
        async def call(self, messages, stream=False):
            raise Exception("Error calling model: Connection error., URL: http://127.0.0.1:8788/v1")

    monkeypatch.setattr(translation, "AcademicPaperRepository", lambda: FakePaperRepository())
    monkeypatch.setattr(
        translation, "KnowledgeChunkRepository", lambda: FakeChunkRepository([_chunk("c1", "A", index=1)])
    )
    monkeypatch.setattr(translation, "select_model", lambda **kwargs: UnreachableModel())

    with pytest.raises(translation.ResearchPaperTranslationError) as exc_info:
        await translation.translate_paper(
            kb_id="kb-1", paper_id="paper-1", current_user=SimpleNamespace(uid="user-1"), model_spec="test:model"
        )

    assert exc_info.value.error_type == "translation_model_unreachable"
    assert exc_info.value.message == "翻译模型连接失败，请确认模型服务是否可用"


class FakeTranslationContext:
    def __init__(self, payload):
        self.payload = payload
        self.progress: list[tuple[float, str | None]] = []
        self.result = None

    async def set_progress(self, progress, message=None):
        self.progress.append((progress, message))

    async def set_result(self, result):
        self.result = result


@pytest.mark.asyncio
async def test_translation_task_reports_progress_and_stores_result(monkeypatch):
    rows = [_chunk("c1", "第一段", index=1), _chunk("c2", "第二段", index=2)]
    selected = [translation._chunk_row(row) for row in rows]
    _, selected = translation.build_translation_context(selected)

    class FakeUserRepository:
        async def get_by_uid(self, uid):
            return SimpleNamespace(uid=uid, is_deleted=False)

    async def prepare(**kwargs):
        return selected, "test:model"

    contents = iter(
        [
            '{"paragraphs":[{"chunk_id":"c1","translation":"甲"}]}',
            '{"paragraphs":[{"chunk_id":"c2","translation":"乙"}]}',
        ]
    )
    monkeypatch.setattr(translation, "UserRepository", FakeUserRepository)
    monkeypatch.setattr(translation, "_prepare_translation", prepare)
    monkeypatch.setattr(translation, "select_model", lambda **kwargs: FakeModel(next(contents)))

    context = FakeTranslationContext(
        {"kb_id": "kb-1", "paper_id": "paper-1", "uid": "user-1", "section_type": None, "limit": 2}
    )
    result = await translation.run_translation_task(context)

    assert result["paragraph_count"] == 2
    assert context.result == result
    messages = [message for _, message in context.progress]
    assert messages[0] == "正在准备论文片段"
    assert "已翻译 1/2 段" in messages
    assert "已翻译 2/2 段" in messages
    assert context.progress[-1] == (100, "论文对照翻译完成")
    progress_values = [value for value, _ in context.progress]
    assert progress_values == sorted(progress_values)


@pytest.mark.asyncio
async def test_enqueue_translation_reuses_active_task(monkeypatch):
    captured = {}

    class FakePaper:
        title = "Attention Is All You Need"

    async def load_paper(**kwargs):
        return FakePaper()

    async def enqueue(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id="task-1", status="running", progress=40.0), False

    monkeypatch.setattr(translation, "_load_translatable_paper", load_paper)
    monkeypatch.setattr(translation.tasker, "enqueue_unique_by_payload", enqueue)

    result = await translation.enqueue_paper_translation(
        kb_id="kb-1",
        paper_id="paper-1",
        current_user=SimpleNamespace(uid="user-1"),
        section_type="methodology",
        limit=99,
    )

    assert result == {"task_id": "task-1", "status": "running", "progress": 40.0, "reused": True}
    assert captured["task_type"] == translation.TRANSLATION_TASK_TYPE
    assert captured["payload_match"] == {"kb_id": "kb-1", "paper_id": "paper-1"}
    assert captured["statuses"] == {"pending", "running"}
    assert captured["payload"]["limit"] == translation.MAX_TRANSLATION_CHUNKS
    assert captured["payload"]["uid"] == "user-1"
    assert captured["coroutine"] is translation.run_translation_task


@pytest.mark.asyncio
async def test_get_translation_task_rejects_foreign_task(monkeypatch, allow_access):
    base_task = {
        "id": "task-1",
        "type": translation.TRANSLATION_TASK_TYPE,
        "status": "running",
        "progress": 30.0,
        "message": "已翻译 1/3 段",
        "error": None,
        "result": None,
        "payload": {"kb_id": "kb-1", "paper_id": "paper-1", "uid": "user-1"},
    }

    async def get_task(task_id):
        return base_task

    monkeypatch.setattr(translation.tasker, "get_task", get_task)

    with pytest.raises(translation.ResearchPaperTranslationError) as forbidden:
        await translation.get_translation_task(
            kb_id="kb-1", paper_id="paper-1", task_id="task-1", current_user=SimpleNamespace(uid="user-2")
        )
    assert forbidden.value.error_type == "forbidden"

    with pytest.raises(translation.ResearchPaperTranslationError) as not_found:
        await translation.get_translation_task(
            kb_id="kb-1", paper_id="paper-2", task_id="task-1", current_user=SimpleNamespace(uid="user-1")
        )
    assert not_found.value.error_type == "translation_task_not_found"

    view = await translation.get_translation_task(
        kb_id="kb-1", paper_id="paper-1", task_id="task-1", current_user=SimpleNamespace(uid="user-1")
    )
    assert view == {
        "task_id": "task-1",
        "status": "running",
        "progress": 30.0,
        "message": "已翻译 1/3 段",
        "error": None,
        "result": None,
    }


@pytest.mark.asyncio
async def test_get_latest_translation_task_reads_owner_record(monkeypatch, allow_access):
    captured = {}

    class FakeTaskRepository:
        async def find_by_payload(self, *, task_type, payload_match):
            captured["task_type"] = task_type
            captured["payload_match"] = payload_match
            return SimpleNamespace(
                to_dict=lambda: {
                    "id": "task-9",
                    "status": "success",
                    "progress": 100.0,
                    "message": "论文对照翻译完成",
                    "error": None,
                    "result": {"paragraphs": [{"chunk_id": "c1"}]},
                }
            )

    monkeypatch.setattr(translation, "TaskRepository", FakeTaskRepository)

    view = await translation.get_latest_translation_task(
        kb_id="kb-1", paper_id="paper-1", current_user=SimpleNamespace(uid="user-1")
    )

    assert captured["task_type"] == translation.TRANSLATION_TASK_TYPE
    assert captured["payload_match"] == {"kb_id": "kb-1", "paper_id": "paper-1", "uid": "user-1"}
    assert view["task_id"] == "task-9"
    assert view["result"] == {"paragraphs": [{"chunk_id": "c1"}]}
