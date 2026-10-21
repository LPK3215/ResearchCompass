"""ResearchCompass 论文对照翻译服务。

按论文切片逐段生成中英对照，返回分块、页码与章节定位，供阅读时对照原文；
翻译结果不写入数据库，也不进入证据库，避免把模型译文当成可引用证据。
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from yuxi.config import config
from yuxi.models import select_model
from yuxi.repositories.academic_paper_repository import AcademicPaperRepository
from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository
from yuxi.repositories.knowledge_chunk_repository import KnowledgeChunkRepository
from yuxi.services.research_paper_service import _ensure_access
from yuxi.storage.postgres.models_business import User

MAX_TRANSLATION_CHUNKS = 12
MAX_TRANSLATION_SOURCE_CHARS = 16000
TRANSLATION_BATCH_SIZE = 1
TRANSLATION_MODEL_TIMEOUT_SECONDS = 60.0

TRANSLATION_SYSTEM_PROMPT = (
    "你是学术论文翻译助手。把给定论文片段逐段译成中文，保留术语、数字与引用标记，不增删、不总结。"
    "只输出合法 JSON 对象，不要 Markdown 代码块与额外解释："
    '{"paragraphs":[{"chunk_id":"片段ID","translation":"中文译文"}]}。'
    "每个输入片段都必须给出对应译文；片段没有文字内容时跳过，不要编造。"
)


class ResearchPaperTranslationError(RuntimeError):
    """论文对照翻译的可预期失败。"""

    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message


def _parse_translation(content: str) -> dict[str, Any]:
    value = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(content or "").strip(), flags=re.IGNORECASE | re.DOTALL)
    try:
        payload = json.loads(value.strip())
    except json.JSONDecodeError as exc:
        raise ResearchPaperTranslationError("translation_invalid_json", "翻译模型返回的 JSON 不可解析") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("paragraphs"), list):
        raise ResearchPaperTranslationError("translation_invalid_schema", "翻译结果必须包含 paragraphs 数组")
    return payload


def _resolve_model_spec(model_spec: str | None, kb_model_spec: str | None) -> str:
    for candidate in (model_spec, kb_model_spec, getattr(config, "default_model", "")):
        resolved = (candidate or "").strip()
        if resolved:
            return resolved
    raise ResearchPaperTranslationError("translation_model_unavailable", "未配置可用的对话模型")


def _chunk_row(chunk: Any) -> dict[str, Any]:
    metadata = chunk.chunk_metadata if isinstance(chunk.chunk_metadata, dict) else {}
    return {
        "chunk_id": str(chunk.chunk_id),
        "chunk_index": int(chunk.chunk_index or 0),
        "source": str(chunk.content or "").strip(),
        "section_title": metadata.get("section_title"),
        "section_type": metadata.get("section_type"),
        "page_start": metadata.get("source_page_start"),
        "page_end": metadata.get("source_page_end"),
    }


def build_translation_context(rows: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """把切片渲染为带 chunk ID 与页码的上下文，超出字符预算时截断尾部片段。"""
    blocks: list[str] = []
    selected: list[dict[str, Any]] = []
    used = 0
    for row in rows:
        source = str(row.get("source") or "").strip()
        if not source:
            continue
        header = f"[chunk:{row['chunk_id']}]"
        if row["page_start"]:
            header += f" 第 {row['page_start']} 页"
        if row["section_title"]:
            header += f" · {row['section_title']}"
        block = f"{header}\n{source}"
        if used + len(block) > MAX_TRANSLATION_SOURCE_CHARS and selected:
            break
        blocks.append(block)
        selected.append({**row, "source": source})
        used += len(block)
    return "\n\n".join(blocks), selected


async def translate_paper(
    *,
    kb_id: str,
    paper_id: str,
    current_user: User,
    section_type: str | None = None,
    limit: int = 6,
    model_spec: str | None = None,
) -> dict[str, Any]:
    """生成单篇论文的逐段中英对照，保留页码与章节定位。"""
    normalized_limit = min(max(int(limit), 1), MAX_TRANSLATION_CHUNKS)
    await _ensure_access(current_user, kb_id)
    paper = await AcademicPaperRepository().get_by_paper_id(kb_id=kb_id, paper_id=paper_id)
    if paper is None:
        raise ResearchPaperTranslationError("paper_not_found", "论文不存在")
    if not paper.file_id:
        raise ResearchPaperTranslationError("paper_chunk_missing", "该论文只有元数据，没有可翻译的正文切片")

    chunks, _ = await KnowledgeChunkRepository().list_academic_by_file_id(
        file_id=str(paper.file_id),
        section_type=section_type,
        offset=0,
        limit=normalized_limit,
    )
    _, selected = build_translation_context([_chunk_row(chunk) for chunk in chunks])
    if not selected:
        raise ResearchPaperTranslationError("paper_chunk_missing", "没有可翻译的正文片段，请先完成解析与索引")

    kb = await KnowledgeBaseRepository().get_by_kb_id(kb_id)
    resolved_model = _resolve_model_spec(model_spec, kb.llm_model_spec if kb else None)
    translations: dict[str, str] = {}
    for start in range(0, len(selected), TRANSLATION_BATCH_SIZE):
        batch = selected[start : start + TRANSLATION_BATCH_SIZE]
        batch_context, _ = build_translation_context(batch)
        messages = [
            {"role": "system", "content": TRANSLATION_SYSTEM_PROMPT},
            {"role": "user", "content": f"论文片段：\n{batch_context}"},
        ]
        try:
            response = await asyncio.wait_for(
                select_model(model_spec=resolved_model, model_params={"temperature": 0}).call(
                    messages, stream=False
                ),
                timeout=TRANSLATION_MODEL_TIMEOUT_SECONDS,
            )
        except TimeoutError as exc:
            batch_no = start // TRANSLATION_BATCH_SIZE + 1
            raise ResearchPaperTranslationError(
                "translation_model_timeout",
                f"论文翻译第 {batch_no} 批超时，请减少片段数或选择章节后重试",
            ) from exc
        except Exception as exc:  # noqa: BLE001 - 模型异常统一转为可定位错误
            raise ResearchPaperTranslationError("translation_model_failed", "论文翻译模型调用失败") from exc

        payload = _parse_translation(getattr(response, "content", ""))
        translations.update(
            {
                str(item.get("chunk_id")): str(item.get("translation") or "").strip()
                for item in payload["paragraphs"]
                if isinstance(item, dict) and item.get("chunk_id") and str(item.get("translation") or "").strip()
            }
        )
    paragraphs = [
        {
            "chunk_id": row["chunk_id"],
            "chunk_index": row["chunk_index"],
            "section_title": row["section_title"],
            "section_type": row["section_type"],
            "page_start": row["page_start"],
            "page_end": row["page_end"],
            "source": row["source"],
            "translation": translations[row["chunk_id"]],
        }
        for row in selected
        if row["chunk_id"] in translations
    ]
    if not paragraphs:
        raise ResearchPaperTranslationError("translation_invalid_schema", "翻译结果没有匹配任何输入片段")
    return {
        "paper_id": paper_id,
        "section_type": section_type,
        "model": resolved_model,
        "paragraph_count": len(paragraphs),
        "requested_count": len(selected),
        "missing_chunk_ids": [row["chunk_id"] for row in selected if row["chunk_id"] not in translations],
        "paragraphs": paragraphs,
        "note": "对照翻译只用于阅读辅助，不写入数据库与证据库，不能作为引用依据。",
    }


__all__ = [
    "MAX_TRANSLATION_CHUNKS",
    "MAX_TRANSLATION_SOURCE_CHARS",
    "TRANSLATION_SYSTEM_PROMPT",
    "ResearchPaperTranslationError",
    "build_translation_context",
    "translate_paper",
]
