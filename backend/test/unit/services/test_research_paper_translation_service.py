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
