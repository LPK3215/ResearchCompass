"""ResearchCompass 论文对照翻译服务。

按论文切片逐段生成中英对照，返回分块、页码与章节定位，供阅读时对照原文；
界面走后台任务（带进度）拿到结果，AI 工具走同步入口，两条入口共用同一批处理；
翻译结果不写入数据库，也不进入证据库，避免把模型译文当成可引用证据。
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException
from yuxi.config import config
from yuxi.models import select_model
from yuxi.repositories.academic_paper_repository import AcademicPaperRepository
from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository
from yuxi.repositories.knowledge_chunk_repository import KnowledgeChunkRepository
from yuxi.repositories.task_repository import TaskRepository
from yuxi.repositories.user_repository import UserRepository
from yuxi.services.research_paper_service import _ensure_access
from yuxi.services.task_service import PublicTaskError, TaskContext, tasker
from yuxi.storage.postgres.models_business import User

MAX_TRANSLATION_CHUNKS = 12
MAX_TRANSLATION_SOURCE_CHARS = 16000
TRANSLATION_BATCH_SIZE = 1
TRANSLATION_MODEL_TIMEOUT_SECONDS = 60.0
TRANSLATION_TASK_TYPE = "research_paper_translation"
TRANSLATION_TASK_TIMEOUT_SECONDS = 30 * 60

TRANSLATION_SYSTEM_PROMPT = (
    "你是学术论文翻译助手。把给定论文片段逐段译成中文，保留术语、数字与引用标记，不增删、不总结。"
    "只输出合法 JSON 对象，不要 Markdown 代码块与额外解释："
    '{"paragraphs":[{"chunk_id":"片段ID","translation":"中文译文"}]}。'
    "每个输入片段都必须给出对应译文；片段没有文字内容时跳过，不要编造。"
)


class ResearchPaperTranslationError(PublicTaskError):
    """论文对照翻译的可预期失败；消息可直接展示给用户（含任务详情）。"""

    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type


def _is_connection_failure(exc: Exception) -> bool:
    """判断模型调用失败是否属于连不上服务。

    模型层会把上游异常统一包成 `Exception("Error calling model: ...")`，所以除了异常类型
    还要看关键字；只做分类，不回显原始报错文本。
    """
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return True
    haystack = f"{type(exc).__name__} {exc}".casefold()
    return any(token in haystack for token in ("connect", "timed out", "timeout"))


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


async def _load_translatable_paper(*, kb_id: str, paper_id: str, current_user: User) -> Any:
    """校验库权限与论文可翻译性，返回论文记录。"""
    await _ensure_access(current_user, kb_id)
    paper = await AcademicPaperRepository().get_by_paper_id(kb_id=kb_id, paper_id=paper_id)
    if paper is None:
        raise ResearchPaperTranslationError("paper_not_found", "论文不存在")
    if not paper.file_id:
        raise ResearchPaperTranslationError("paper_chunk_missing", "该论文只有元数据，没有可翻译的正文切片")
    return paper


async def _prepare_translation(
    *,
    kb_id: str,
    paper_id: str,
    current_user: User,
    section_type: str | None,
    limit: int,
    model_spec: str | None,
) -> tuple[list[dict[str, Any]], str]:
    """取可翻译片段并解析模型，返回 (片段, 模型标识)。"""
    paper = await _load_translatable_paper(kb_id=kb_id, paper_id=paper_id, current_user=current_user)
    normalized_limit = min(max(int(limit), 1), MAX_TRANSLATION_CHUNKS)
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
    return selected, _resolve_model_spec(model_spec, kb.llm_model_spec if kb else None)


async def _translate_selected(
    selected: list[dict[str, Any]],
    resolved_model: str,
    *,
    on_batch_done: Callable[[int, int], Awaitable[None]] | None = None,
) -> dict[str, str]:
    """按批调用模型，返回 chunk_id → 译文；超时与模型异常转成可定位错误。"""
    translations: dict[str, str] = {}
    total = len(selected)
    for done, start in enumerate(range(0, total, TRANSLATION_BATCH_SIZE)):
        batch = selected[start : start + TRANSLATION_BATCH_SIZE]
        batch_context, _ = build_translation_context(batch)
        messages = [
            {"role": "system", "content": TRANSLATION_SYSTEM_PROMPT},
            {"role": "user", "content": f"论文片段：\n{batch_context}"},
        ]
        try:
            response = await asyncio.wait_for(
                select_model(model_spec=resolved_model, model_params={"temperature": 0}).call(messages, stream=False),
                timeout=TRANSLATION_MODEL_TIMEOUT_SECONDS,
            )
        except TimeoutError as exc:
            raise ResearchPaperTranslationError(
                "translation_model_timeout",
                f"论文翻译第 {done + 1} 批超时，请减少片段数或选择章节后重试",
            ) from exc
        except Exception as exc:  # noqa: BLE001 - 模型异常统一转为可定位错误
            if _is_connection_failure(exc):
                raise ResearchPaperTranslationError(
                    "translation_model_unreachable", "翻译模型连接失败，请确认模型服务是否可用"
                ) from exc
            raise ResearchPaperTranslationError("translation_model_failed", "论文翻译模型调用失败") from exc

        payload = _parse_translation(getattr(response, "content", ""))
        translations.update(
            {
                str(item.get("chunk_id")): str(item.get("translation") or "").strip()
                for item in payload["paragraphs"]
                if isinstance(item, dict) and item.get("chunk_id") and str(item.get("translation") or "").strip()
            }
        )
        if on_batch_done is not None:
            await on_batch_done(done + 1, total)
    return translations


def _build_translation_payload(
    *,
    paper_id: str,
    section_type: str | None,
    resolved_model: str,
    selected: list[dict[str, Any]],
    translations: dict[str, str],
) -> dict[str, Any]:
    """把片段与译文组装成对照结果（界面与 AI 工具共用）。"""
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


async def translate_paper(
    *,
    kb_id: str,
    paper_id: str,
    current_user: User,
    section_type: str | None = None,
    limit: int = 6,
    model_spec: str | None = None,
) -> dict[str, Any]:
    """同步生成单篇论文的逐段中英对照（AI 工具使用），保留页码与章节定位。"""
    selected, resolved_model = await _prepare_translation(
        kb_id=kb_id,
        paper_id=paper_id,
        current_user=current_user,
        section_type=section_type,
        limit=limit,
        model_spec=model_spec,
    )
    translations = await _translate_selected(selected, resolved_model)
    return _build_translation_payload(
        paper_id=paper_id,
        section_type=section_type,
        resolved_model=resolved_model,
        selected=selected,
        translations=translations,
    )


async def enqueue_paper_translation(
    *,
    kb_id: str,
    paper_id: str,
    current_user: User,
    section_type: str | None = None,
    limit: int = 6,
    model_spec: str | None = None,
) -> dict[str, Any]:
    """提交后台对照翻译任务；同一论文已有进行中任务时复用该任务。"""
    paper = await _load_translatable_paper(kb_id=kb_id, paper_id=paper_id, current_user=current_user)
    normalized_limit = min(max(int(limit), 1), MAX_TRANSLATION_CHUNKS)
    task, created = await tasker.enqueue_unique_by_payload(
        name=f"论文对照翻译 ({str(paper.title or paper_id)[:40]})",
        task_type=TRANSLATION_TASK_TYPE,
        payload={
            "kb_id": kb_id,
            "paper_id": paper_id,
            "uid": str(current_user.uid),
            "section_type": section_type,
            "limit": normalized_limit,
            "model_spec": model_spec,
        },
        payload_match={"kb_id": kb_id, "paper_id": paper_id},
        statuses={"pending", "running"},
        timeout_seconds=TRANSLATION_TASK_TIMEOUT_SECONDS,
        coroutine=run_translation_task,
    )
    return {"task_id": task.id, "status": task.status, "progress": task.progress, "reused": not created}


async def run_translation_task(context: TaskContext) -> dict[str, Any]:
    """执行后台对照翻译：逐批上报进度，完成后把结果写进任务记录。"""
    payload = context.payload if isinstance(context.payload, dict) else {}
    try:
        kb_id = str(payload["kb_id"])
        paper_id = str(payload["paper_id"])
        uid = str(payload["uid"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("论文对照翻译任务参数无效") from exc
    user = await UserRepository().get_by_uid(uid)
    if user is None or bool(user.is_deleted):
        raise ValueError("论文对照翻译任务发起人不存在或已删除")

    section_type = payload.get("section_type")
    await context.set_progress(5, "正在准备论文片段")
    try:
        selected, resolved_model = await _prepare_translation(
            kb_id=kb_id,
            paper_id=paper_id,
            current_user=user,
            section_type=section_type,
            limit=int(payload.get("limit") or 6),
            model_spec=payload.get("model_spec"),
        )
    except HTTPException as exc:
        raise ResearchPaperTranslationError("forbidden", "任务发起人已失去该知识库的访问权限") from exc

    async def report(done: int, total: int) -> None:
        await context.set_progress(5 + 90 * done / total, f"已翻译 {done}/{total} 段")

    translations = await _translate_selected(selected, resolved_model, on_batch_done=report)
    result = _build_translation_payload(
        paper_id=paper_id,
        section_type=section_type,
        resolved_model=resolved_model,
        selected=selected,
        translations=translations,
    )
    await context.set_result(result)
    await context.set_progress(100, "论文对照翻译完成")
    return result


async def get_translation_task(
    *, kb_id: str, paper_id: str, task_id: str, current_user: User
) -> dict[str, Any]:
    """读取对照翻译任务状态与结果；只允许发起人查看本库本篇的任务。"""
    task = await tasker.get_task(task_id)
    payload = (task or {}).get("payload") or {}
    if (
        task is None
        or task.get("type") != TRANSLATION_TASK_TYPE
        or payload.get("kb_id") != kb_id
        or payload.get("paper_id") != paper_id
    ):
        raise ResearchPaperTranslationError("translation_task_not_found", "对照翻译任务不存在")
    if str(payload.get("uid")) != str(current_user.uid):
        raise ResearchPaperTranslationError("forbidden", "无权查看该对照翻译任务")
    await _ensure_access(current_user, kb_id)
    return _translation_task_view(task)


async def get_latest_translation_task(
    *, kb_id: str, paper_id: str, current_user: User
) -> dict[str, Any] | None:
    """读取当前用户在该论文上最近一次对照翻译任务，供重新打开抽屉时恢复进度或结果。"""
    await _ensure_access(current_user, kb_id)
    record = await TaskRepository().find_by_payload(
        task_type=TRANSLATION_TASK_TYPE,
        payload_match={"kb_id": kb_id, "paper_id": paper_id, "uid": str(current_user.uid)},
    )
    return _translation_task_view(record.to_dict()) if record is not None else None


def _translation_task_view(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": task["id"],
        "status": task.get("status"),
        "progress": task.get("progress") or 0.0,
        "message": task.get("message") or "",
        "error": task.get("error"),
        "result": task.get("result") if task.get("status") == "success" else None,
    }


__all__ = [
    "MAX_TRANSLATION_CHUNKS",
    "MAX_TRANSLATION_SOURCE_CHARS",
    "TRANSLATION_SYSTEM_PROMPT",
    "TRANSLATION_TASK_TYPE",
    "ResearchPaperTranslationError",
    "build_translation_context",
    "enqueue_paper_translation",
    "get_latest_translation_task",
    "get_translation_task",
    "run_translation_task",
    "translate_paper",
]

tasker.register_resumable_handler(TRANSLATION_TASK_TYPE, run_translation_task)
