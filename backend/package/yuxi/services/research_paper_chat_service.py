"""ResearchCompass 单篇论文对话服务。

阅读阶段的追问链路：检索范围固定为当前论文的学术切片，回答必须引用命中的
chunk，引用信息带回页码与章节，供界面跳回 PDF 原文。界面与 AI 工具共用本
服务，因此两者的检索范围与引用口径一致。
"""

from __future__ import annotations

import re
from typing import Any

from yuxi.config import config
from yuxi.knowledge.runtime import knowledge_base
from yuxi.models import select_model
from yuxi.repositories.academic_paper_repository import AcademicPaperRepository
from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository
from yuxi.repositories.knowledge_chunk_repository import KnowledgeChunkRepository
from yuxi.services.research_paper_service import _ensure_access
from yuxi.services.research_search_service import _is_embedding_failure, _resolve_chat_model
from yuxi.storage.postgres.models_business import User

MAX_QUESTION_CHARS = 2000
MAX_HISTORY_MESSAGES = 6
MAX_CONTEXT_CHARS = 12000
DEFAULT_TOP_K = 6
CITATION_PATTERN = re.compile(r"\[chunk:([A-Za-z0-9_\-]+)\]")

PAPER_CHAT_SYSTEM_PROMPT = (
    "你是论文精读助手，只依据给出的论文片段回答，不使用外部知识。"
    "每条关键结论后必须用 [chunk:片段ID] 标注来源，片段 ID 只能来自给定片段列表。"
    "片段没有提供的信息直接说明“原文未提及”，不要推测；回答用中文，结构清晰，可适度引用原文。"
)


class ResearchPaperChatError(RuntimeError):
    """论文对话的可预期失败。"""

    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message


def _normalize_history(history: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for item in history or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip()
        content = str(item.get("content") or "").strip()
        if role not in {"user", "assistant"} or not content:
            raise ResearchPaperChatError("paper_chat_history_invalid", "对话历史只接受 user / assistant 的非空消息")
        messages.append({"role": role, "content": content[:4000]})
    return messages[-MAX_HISTORY_MESSAGES:]


def _chunk_text(chunk: Any) -> str:
    return str(chunk.get("content") or "").strip() if isinstance(chunk, dict) else ""


def _chunk_metadata(chunk: dict[str, Any]) -> dict[str, Any]:
    metadata = chunk.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _chunk_identity(chunk: dict[str, Any]) -> tuple[str, Any]:
    """兼容运行时 chunk 结构：知识库召回把 chunk_id / chunk_index 放在 metadata 内。"""
    metadata = _chunk_metadata(chunk)
    chunk_id = str(chunk.get("chunk_id") or metadata.get("chunk_id") or "")
    chunk_index = chunk.get("chunk_index")
    if chunk_index is None:
        chunk_index = metadata.get("chunk_index")
    return chunk_id, chunk_index


async def _retrieve_paper_chunks(*, kb_id: str, file_id: str, question: str, top_k: int) -> list[dict[str, Any]]:
    try:
        return await knowledge_base.aquery(
            question,
            kb_id,
            strict_research=True,
            search_mode="hybrid",
            recall_top_k=max(top_k * 4, 20),
            final_top_k=top_k,
            use_reranker=True,
            reranker_model=config.reranker,
            use_graph_retrieval=False,
            file_ids=[file_id],
            include_distances=True,
            similarity_threshold=0.0,
        )
    except Exception as exc:
        if not _is_embedding_failure(exc):
            raise ResearchPaperChatError("paper_chat_retrieval_failed", "论文片段检索失败") from exc
        try:
            return await knowledge_base.aquery(
                question,
                kb_id,
                strict_research=False,
                search_mode="keyword",
                recall_top_k=max(top_k * 4, 20),
                final_top_k=top_k,
                use_reranker=False,
                use_graph_retrieval=False,
                file_ids=[file_id],
                include_distances=True,
                similarity_threshold=0.0,
            )
        except Exception as fallback_exc:
            raise ResearchPaperChatError(
                "paper_chat_retrieval_failed", "论文片段检索与关键词降级均失败"
            ) from fallback_exc


def render_chunk_context(chunks: list[dict[str, Any]], metadata: dict[str, Any]) -> str:
    """把命中片段渲染为带 chunk ID 与页码的上下文，超出预算时按序截断。"""
    blocks: list[str] = []
    used = 0
    for chunk in chunks:
        chunk_id, _ = _chunk_identity(chunk)
        content = _chunk_text(chunk)
        if not chunk_id or not content:
            continue
        stored = metadata.get(chunk_id)
        meta = stored if isinstance(stored, dict) else _chunk_metadata(chunk)
        page = meta.get("source_page_start")
        section = str(meta.get("section_title") or meta.get("section_type") or "").strip()
        header = f"[chunk:{chunk_id}]" + (f" 第 {page} 页" if page else "") + (f" · {section}" if section else "")
        block = f"{header}\n{content}"
        if used + len(block) > MAX_CONTEXT_CHARS:
            break
        blocks.append(block)
        used += len(block)
    return "\n\n".join(blocks)


def _citations(answer: str, chunks: list[dict[str, Any]], metadata: dict[str, Any]) -> list[dict[str, Any]]:
    referenced = list(dict.fromkeys(CITATION_PATTERN.findall(answer)))
    by_id: dict[str, dict[str, Any]] = {}
    indexes: dict[str, Any] = {}
    for chunk in chunks:
        chunk_id, chunk_index = _chunk_identity(chunk)
        if chunk_id:
            by_id[chunk_id] = chunk
            indexes[chunk_id] = chunk_index
    ordered = [chunk_id for chunk_id in referenced if chunk_id in by_id]
    ordered.extend(chunk_id for chunk_id in by_id if chunk_id not in ordered)
    citations = []
    for chunk_id in ordered:
        meta = metadata.get(chunk_id) if isinstance(metadata.get(chunk_id), dict) else _chunk_metadata(by_id[chunk_id])
        content = _chunk_text(by_id[chunk_id])
        citations.append(
            {
                "chunk_id": chunk_id,
                "cited": chunk_id in referenced,
                "chunk_index": indexes.get(chunk_id),
                "section_title": meta.get("section_title"),
                "section_type": meta.get("section_type"),
                "page_start": meta.get("source_page_start"),
                "page_end": meta.get("source_page_end"),
                "snippet": content[:220],
            }
        )
    return citations


async def _resolve_chat_model_for_kb(kb_id: str, override: str | None) -> str:
    kb = await KnowledgeBaseRepository().get_by_kb_id(kb_id)
    model_spec = _resolve_chat_model(override=override, kb_llm_model_spec=kb.llm_model_spec if kb else None)
    if not model_spec:
        raise ResearchPaperChatError("paper_chat_model_unavailable", "未配置可用的对话模型")
    return model_spec


async def ask_paper_question(
    *,
    kb_id: str,
    paper_id: str,
    question: str,
    current_user: User,
    history: list[dict[str, Any]] | None = None,
    chat_model: str | None = None,
    top_k: int = DEFAULT_TOP_K,
) -> dict[str, Any]:
    """在单篇论文范围内追问，回答携带 chunk 引用与页码定位。"""
    normalized_question = str(question or "").strip()
    if not normalized_question or len(normalized_question) > MAX_QUESTION_CHARS:
        raise ResearchPaperChatError(
            "paper_chat_invalid_question", f"问题长度必须在 1 到 {MAX_QUESTION_CHARS} 个字符之间"
        )
    history_messages = _normalize_history(history)
    await _ensure_access(current_user, kb_id)
    paper = await AcademicPaperRepository().get_by_paper_id(kb_id=kb_id, paper_id=paper_id)
    if paper is None:
        raise ResearchPaperChatError("paper_not_found", "论文不存在")
    if not paper.file_id:
        raise ResearchPaperChatError("paper_chunk_missing", "该论文只有元数据，没有可对话的正文切片")

    chunks = await _retrieve_paper_chunks(
        kb_id=kb_id, file_id=str(paper.file_id), question=normalized_question, top_k=min(max(top_k, 1), 12)
    )
    if not chunks:
        raise ResearchPaperChatError("paper_chunk_missing", "该论文没有可检索的学术切片，请先完成解析与索引")
    chunk_ids = [chunk_id for chunk_id, _ in map(_chunk_identity, chunks) if chunk_id]
    stored = await KnowledgeChunkRepository().list_by_chunk_ids(chunk_ids)
    metadata = {
        chunk.chunk_id: chunk.chunk_metadata if isinstance(chunk.chunk_metadata, dict) else {} for chunk in stored
    }
    context = render_chunk_context(chunks, metadata)
    if not context:
        raise ResearchPaperChatError("paper_chunk_missing", "该论文没有可用于回答的正文片段")

    model_spec = await _resolve_chat_model_for_kb(kb_id, chat_model)
    messages = [
        {"role": "system", "content": PAPER_CHAT_SYSTEM_PROMPT},
        *history_messages,
        {"role": "user", "content": f"论文片段：\n{context}\n\n问题：{normalized_question}"},
    ]
    try:
        response = await select_model(model_spec=model_spec, model_params={"temperature": 0.2}).call(
            messages, stream=False
        )
    except Exception as exc:  # noqa: BLE001 - 模型异常统一转为可定位错误
        raise ResearchPaperChatError("paper_chat_model_failed", "论文对话模型调用失败") from exc
    answer = str(getattr(response, "content", "") or "").strip()
    if not answer:
        raise ResearchPaperChatError("paper_chat_empty_answer", "模型没有返回有效回答")
    return {
        "paper_id": paper_id,
        "question": normalized_question,
        "answer": answer,
        "citations": _citations(answer, chunks, metadata),
        "model": model_spec,
        "retrieved_chunk_count": len(chunks),
    }


__all__ = [
    "CITATION_PATTERN",
    "PAPER_CHAT_SYSTEM_PROMPT",
    "ResearchPaperChatError",
    "ask_paper_question",
    "render_chunk_context",
]
