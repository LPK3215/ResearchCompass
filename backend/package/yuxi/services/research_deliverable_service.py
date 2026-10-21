"""科研交付物服务：把论文元数据、分析结果与项目资料渲染为可交付文件。

本模块只做“已有数据 -> 文件”的确定性渲染，以及需要模型参与的两类生成（分层摘要、汇报包）。
模型调用沿用论文分析服务的约定：单次调用、严格 JSON、失败显式报错。
"""

from __future__ import annotations

import asyncio
import html
import json
import re
from typing import Any

from yuxi.config import config
from yuxi.models import select_model
from yuxi.repositories.academic_paper_repository import AcademicPaperRepository
from yuxi.services.research_paper_service import _ensure_access
from yuxi.storage.postgres.models_business import User
from yuxi.utils.datetime_utils import utc_now_naive

EXPORT_FORMATS = {
    "markdown": ("md", "text/markdown; charset=utf-8"),
    "json": ("json", "application/json; charset=utf-8"),
}
MAX_PACK_PAPERS = 10
MAX_CONTEXT_CHARS = 20000

BRIEF_SYSTEM_PROMPT = (
    "你是科研阅读助手。只依据给定论文正文生成分层摘要，不要补充外部知识。"
    "输出合法 JSON 对象，不要 Markdown 代码块，不要额外解释："
    '{"one_sentence":"一句话结论，不超过40字",'
    '"paragraph":"一段摘要，150到200字",'
    '"page":"一页摘要，400到600字，覆盖研究问题、方法、数据、结论与局限",'
    '"keywords":["..."],"limitations":["..."],"confidence":"high|medium|low"}。'
    "正文没有提供的信息写“原文未说明”，不要猜测。"
)

PACK_SYSTEM_PROMPT = (
    "你是学术汇报策划。只依据给定论文资料组织汇报，不要编造论文与结论。"
    "输出合法 JSON 对象，不要 Markdown 代码块，不要额外解释："
    '{"title":"汇报标题","audience":"听众定位",'
    '"outline":[{"section":"章节名","bullets":["要点"]}],'
    '"comparison":{"columns":["论文","研究问题","方法","数据","结论","局限"],"rows":[["..."]]},'
    '"talking_points":["讲稿要点"],"risks":["需要说明的局限或风险"]}。'
    "comparison.rows 每行必须对应给定论文中的一篇，单元格资料不足写“未说明”。"
)


class ResearchDeliverableError(RuntimeError):
    """交付物生成或导出的可预期失败。"""

    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message


def _parse_json_object(content: str, *, stage: str) -> dict[str, Any]:
    value = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.IGNORECASE | re.DOTALL).strip()
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ResearchDeliverableError(f"{stage}_invalid_json", f"{stage} 返回的 JSON 不可解析") from exc
    if not isinstance(payload, dict):
        raise ResearchDeliverableError(f"{stage}_invalid_schema", f"{stage} 必须返回 JSON 对象")
    return payload


def _resolve_model_spec(model_spec: str | None) -> str:
    resolved = (model_spec or getattr(config, "default_model", "") or "").strip()
    if not resolved:
        raise ResearchDeliverableError("deliverable_model_unavailable", "未配置可用的对话模型")
    return resolved


async def _call_model(model_spec: str, system_prompt: str, context: str, *, stage: str) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            model = select_model(model_spec=model_spec, model_params={"temperature": 0})
            response = await model.call(
                [{"role": "system", "content": system_prompt}, {"role": "user", "content": context}],
                stream=False,
            )
            return _parse_json_object(str(response.content or ""), stage=stage)
        except Exception as exc:  # noqa: BLE001 - 外部模型异常统一转为可定位错误
            last_error = exc
            if attempt == 0:
                await asyncio.sleep(1)
                continue
            if isinstance(exc, ResearchDeliverableError):
                raise
            raise ResearchDeliverableError("deliverable_model_failed", "交付物生成模型调用失败") from exc
    raise ResearchDeliverableError("deliverable_model_failed", "交付物生成模型调用失败") from last_error


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _now_text() -> str:
    return utc_now_naive().strftime("%Y-%m-%d %H:%M")


def _paper_meta(paper: Any) -> dict[str, Any]:
    return {
        "paper_id": str(paper.paper_id),
        "file_id": str(paper.file_id or ""),
        "title": str(paper.title or "").strip(),
        "authors": _string_list(paper.authors),
        "year": paper.publication_year,
        "venue": str(paper.venue or "").strip(),
        "doi": str(paper.doi or "").strip(),
        "keywords": _string_list(paper.keywords),
        "citation_count": paper.citation_count,
        "abstract": str(paper.abstract or "").strip(),
    }


def _stage_payload(analysis: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(analysis, dict):
        return {}
    stages = analysis.get("stage_results")
    if isinstance(stages, dict) and stages:
        return stages
    result = analysis.get("result")
    return result if isinstance(result, dict) else {}


def _structure_of(stages: dict[str, Any]) -> dict[str, Any]:
    single = stages.get("single_agent")
    if isinstance(single, dict):
        structure = single.get("structure")
        return structure if isinstance(structure, dict) else {}
    structure = stages.get("structure")
    return structure if isinstance(structure, dict) else {}


def _bullet_lines(values: Any) -> list[str]:
    if isinstance(values, list):
        lines: list[str] = []
        for item in values:
            if isinstance(item, dict):
                text = str(item.get("direction") or item.get("title") or item.get("claim") or "").strip()
                basis = str(item.get("basis") or "").strip()
                confidence = str(item.get("confidence") or "").strip()
                if not text:
                    text = str(item).strip()
                if basis:
                    text += f"（依据：{basis}）"
                if confidence:
                    text += f"（置信度：{confidence}）"
                if text:
                    lines.append(text)
            else:
                text = str(item).strip()
                if text:
                    lines.append(text)
        return lines
    if isinstance(values, str) and values.strip():
        return [values.strip()]
    return []


async def _latest_structure(
    kb_id: str, paper_id: str, current_user: User
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    from yuxi.services.academic_paper_analysis_service import get_latest_paper_analysis

    analysis = await get_latest_paper_analysis(kb_id=kb_id, paper_id=paper_id, current_user=current_user)
    if not analysis:
        return {}, "", {}
    stages = _stage_payload(analysis)
    return _structure_of(stages), str(analysis.get("run_id") or ""), stages


async def _compose_cards(kb_id: str, paper_ids: list[str], current_user: User) -> list[dict[str, Any]]:
    await _ensure_access(current_user, kb_id)
    repo = AcademicPaperRepository()
    if paper_ids:
        papers = await repo.list_by_paper_ids(kb_id=kb_id, paper_ids=paper_ids)
        if len(papers) != len(set(paper_ids)):
            raise ResearchDeliverableError("paper_not_found", "部分论文不存在于当前知识库")
    else:
        papers = []
        async for batch in repo.iter_export_batches(kb_id=kb_id):
            papers.extend(batch)
    cards = []
    for paper in papers:
        structure, analysis_run_id, stages = await _latest_structure(kb_id, str(paper.paper_id), current_user)
        methodology = stages.get("methodology")
        if not isinstance(methodology, dict):
            single = stages.get("single_agent")
            methodology = single.get("methodology") if isinstance(single, dict) else None
        method_steps = _bullet_lines((methodology or {}).get("method_steps"))
        if not method_steps and structure.get("method"):
            method_steps = [str(structure["method"])]
        cards.append(
            {
                **_paper_meta(paper),
                "analysis_run_id": analysis_run_id,
                "analysis": {
                    "problem": str(structure.get("problem") or "").strip(),
                    "method": str(structure.get("method") or "").strip(),
                    "method_steps": method_steps[:12],
                    "datasets": _bullet_lines(structure.get("datasets")),
                    "results": _bullet_lines(structure.get("results")),
                    "limitations": _bullet_lines(structure.get("limitations")),
                },
            }
        )
    return cards


def _render_cards_markdown(cards: list[dict[str, Any]]) -> str:
    lines = ["# 文献卡片", "", f"- 生成时间：{_now_text()}", f"- 论文数：{len(cards)}", ""]
    for index, card in enumerate(cards, start=1):
        lines.extend([f"## {index}. {card['title'] or card['paper_id']}", ""])
        meta = []
        if card["authors"]:
            meta.append("作者：" + "、".join(card["authors"]))
        if card["year"]:
            meta.append(f"年份：{card['year']}")
        if card["venue"]:
            meta.append(f"发表处：{card['venue']}")
        if card["doi"]:
            meta.append(f"DOI：{card['doi']}")
        if card["citation_count"] is not None:
            meta.append(f"被引：{card['citation_count']}")
        if card["keywords"]:
            meta.append("关键词：" + "、".join(card["keywords"]))
        if meta:
            lines.extend(["- " + item for item in meta] + [""])
        analysis = card["analysis"]
        lines.extend(["### 分析摘要", ""])
        if analysis["problem"]:
            lines.extend([f"- 研究问题：{analysis['problem']}"])
        if analysis["method"]:
            lines.extend([f"- 方法：{analysis['method']}"])
        for label, key in (("数据集", "datasets"), ("主要结果", "results"), ("局限", "limitations")):
            values = analysis[key]
            if values:
                lines.append(f"- {label}：" + "；".join(values))
        if not any(analysis.values()):
            lines.append("- 尚未生成论文分析，可在论文详情运行分析后重新导出")
        if card["abstract"]:
            lines.extend(["", "### 摘要原文", "", card["abstract"]])
        lines.append("")
    return "\n".join(lines)


def _render_cards_json(cards: list[dict[str, Any]]) -> str:
    return json.dumps(
        {"generated_at": _now_text(), "paper_count": len(cards), "cards": cards},
        ensure_ascii=False,
        indent=2,
    )


async def export_paper_cards(
    *, kb_id: str, current_user: User, paper_ids: list[str] | None, export_format: str
) -> tuple[str, bytes, str]:
    """导出文献卡片，返回 (filename, content, media_type)。"""
    if export_format not in EXPORT_FORMATS:
        raise ResearchDeliverableError("invalid_export_format", "文献卡片只支持 markdown 或 json")
    cards = await _compose_cards(kb_id, paper_ids or [], current_user)
    if not cards:
        raise ResearchDeliverableError("paper_not_found", "当前知识库还没有可导出的论文")
    ext, media_type = EXPORT_FORMATS[export_format]
    content = _render_cards_markdown(cards) if export_format == "markdown" else _render_cards_json(cards)
    filename = f"research_paper_cards_{utc_now_naive().strftime('%Y%m%d%H%M')}.{ext}"
    return filename, content.encode("utf-8"), media_type


def _render_analysis_markdown(run: dict[str, Any], paper_title: str) -> str:
    stages = _stage_payload(run)
    structure = _structure_of(stages)
    report_title = (
        paper_title
        or str(structure.get("title") or "").strip()
        or str(run.get("paper_id") or "").strip()
        or "未命名论文"
    )
    lines = [
        f"# 论文分析报告：{report_title}",
        "",
        f"- 运行 ID：{run.get('run_id', '')}",
        f"- 状态：{run.get('status', '')}",
        f"- 策略：{run.get('strategy', '')}",
        f"- 生成时间：{_now_text()}",
        "",
    ]
    if structure:
        lines.extend(["## 一、结构化提取", ""])
        if structure.get("title"):
            lines.append(f"- 论文标题：{structure['title']}")
        for label, key in (
            ("研究问题", "problem"),
            ("方法", "method"),
        ):
            if structure.get(key):
                lines.append(f"- {label}：{structure[key]}")
        for label, key in (("数据集", "datasets"), ("实验结果", "results"), ("局限", "limitations")):
            values = _bullet_lines(structure.get(key))
            if values:
                lines.append(f"- {label}：" + "；".join(values))
        lines.append("")
    innovations = (
        stages.get("innovations", {}).get("items")
        if isinstance(stages.get("innovations"), dict)
        else (stages.get("single_agent", {}) or {}).get("innovations", {}).get("items")
    )
    if isinstance(innovations, list) and innovations:
        lines.extend(["## 二、创新点", ""])
        for item in innovations:
            if not isinstance(item, dict):
                continue
            claim = str(item.get("claim") or "").strip()
            evidence = str(item.get("evidence") or "").strip()
            confidence = str(item.get("confidence") or "").strip()
            if claim:
                suffix = f"（置信度：{confidence}）" if confidence else ""
                lines.append(f"- {claim}{suffix}")
                if evidence:
                    lines.append(f"  - 原文证据：{evidence}")
        lines.append("")
    methodology = (
        stages.get("methodology")
        if isinstance(stages.get("methodology"), dict)
        else (stages.get("single_agent", {}) or {}).get("methodology")
    )
    if isinstance(methodology, dict) and methodology:
        lines.extend(["## 三、方法论与可复现性", ""])
        if methodology.get("research_design"):
            lines.append(f"- 研究设计：{methodology['research_design']}")
        for label, key in (("方法步骤", "method_steps"), ("评估", "evaluation")):
            values = _bullet_lines(methodology.get(key))
            if values:
                lines.append(f"- {label}：" + "；".join(values))
        reproducibility = methodology.get("reproducibility")
        if isinstance(reproducibility, dict):
            available = "具备" if reproducibility.get("available") else "未说明"
            details = "；".join(_bullet_lines(reproducibility.get("details")))
            lines.append(f"- 可复现性：{available}{('（' + details + '）') if details else ''}")
        lines.append("")
    gaps = stages.get("gaps")
    if not isinstance(gaps, dict):
        gaps = stages.get("research_gaps")
    if not isinstance(gaps, dict):
        single = stages.get("single_agent")
        gaps = single.get("research_gaps") if isinstance(single, dict) else None
    if isinstance(gaps, dict) and gaps:
        lines.extend(["## 四、研究空白与后续方向", ""])
        for item in gaps.get("gaps") or []:
            if not isinstance(item, dict):
                continue
            claim = str(item.get("claim") or "").strip()
            basis = str(item.get("basis") or "").strip()
            if claim:
                lines.append(f"- {claim}")
                if basis:
                    lines.append(f"  - 依据：{basis}")
        directions = _bullet_lines(gaps.get("future_directions"))
        if directions:
            lines.append("- 后续方向：" + "；".join(directions))
        lines.append("")
    if len(lines) <= 7:
        lines.extend(["该运行暂无可导出的分析内容（可能仍在运行或已失败）。", ""])
    return "\n".join(lines)


async def export_paper_analysis_markdown(*, run_id: str, current_user: User) -> tuple[str, bytes, str]:
    """把一次论文分析运行导出为 Markdown，返回 (filename, content, media_type)。"""
    from yuxi.services.academic_paper_analysis_service import get_paper_analysis_run

    run = await get_paper_analysis_run(run_id=run_id, current_user=current_user)
    paper_title = ""
    kb_id = str(run.get("kb_id") or "")
    paper_id = str(run.get("paper_id") or "")
    if kb_id and paper_id:
        paper = await AcademicPaperRepository().get_by_paper_id(kb_id=kb_id, paper_id=paper_id)
        paper_title = str(getattr(paper, "title", "") or "")
    content = _render_analysis_markdown(run, paper_title).encode("utf-8")
    filename = f"research_paper_analysis_{run_id}.md"
    return filename, content, "text/markdown; charset=utf-8"


async def generate_paper_brief(
    *, kb_id: str, paper_id: str, current_user: User, model_spec: str | None
) -> dict[str, Any]:
    """生成单篇论文的分层摘要（一句话 / 一段 / 一页），返回结构化结果与 Markdown。"""
    from yuxi.services.academic_paper_analysis_service import _paper_context

    await _ensure_access(current_user, kb_id)
    paper, paper_context = await _paper_context(kb_id, paper_id)
    resolved_model = _resolve_model_spec(model_spec)
    payload = await _call_model(
        resolved_model,
        BRIEF_SYSTEM_PROMPT,
        paper_context[:MAX_CONTEXT_CHARS],
        stage="brief",
    )
    brief = {
        "paper_id": str(paper.paper_id),
        "title": str(paper.title or "").strip(),
        "one_sentence": str(payload.get("one_sentence") or "").strip(),
        "paragraph": str(payload.get("paragraph") or "").strip(),
        "page": str(payload.get("page") or "").strip(),
        "keywords": _string_list(payload.get("keywords")),
        "limitations": _string_list(payload.get("limitations")),
        "confidence": str(payload.get("confidence") or "").strip(),
        "model": resolved_model,
    }
    if not brief["one_sentence"] and not brief["paragraph"]:
        raise ResearchDeliverableError("brief_invalid_schema", "分层摘要缺少 one_sentence 与 paragraph")
    return brief


def render_brief_markdown(brief: dict[str, Any]) -> str:
    lines = [
        f"# 分层摘要：{brief.get('title') or brief.get('paper_id', '')}",
        "",
        f"- 生成时间：{_now_text()}",
        f"- 模型：{brief.get('model', '')}",
        "",
        "## 一句话结论",
        "",
        brief.get("one_sentence") or "原文未说明",
        "",
        "## 一段摘要",
        "",
        brief.get("paragraph") or "原文未说明",
        "",
        "## 一页摘要",
        "",
        brief.get("page") or "原文未说明",
    ]
    if brief.get("keywords"):
        lines.extend(["", "## 关键词", "", "、".join(brief["keywords"])])
    if brief.get("limitations"):
        lines.extend(["", "## 局限", ""] + [f"- {item}" for item in brief["limitations"]])
    return "\n".join(lines)


def _pack_context(cards: list[dict[str, Any]]) -> str:
    parts = []
    for card in cards:
        analysis = card.get("analysis") or {}
        parts.append(
            "\n".join(
                [
                    f"论文：{card['title']}（{card.get('year') or '年份未说明'}）",
                    f"作者：{'、'.join(card.get('authors') or []) or '未说明'}",
                    f"发表处：{card.get('venue') or '未说明'}",
                    f"摘要：{(card.get('abstract') or '')[:1200]}",
                    f"研究问题：{analysis.get('problem') or '未说明'}",
                    f"方法：{analysis.get('method') or '未说明'}",
                    f"数据集：{'；'.join(analysis.get('datasets') or []) or '未说明'}",
                    f"结论：{'；'.join(analysis.get('results') or []) or '未说明'}",
                    f"局限：{'；'.join(analysis.get('limitations') or []) or '未说明'}",
                ]
            )
        )
    return "\n\n".join(parts)[:MAX_CONTEXT_CHARS]


async def generate_presentation_pack(
    *, kb_id: str, paper_ids: list[str], current_user: User, model_spec: str | None
) -> dict[str, Any]:
    """基于 1 到 10 篇论文生成汇报包（大纲、对比表、讲稿要点），返回结构化结果与两种渲染。"""
    normalized = list(dict.fromkeys(item.strip() for item in paper_ids if item.strip()))
    if not normalized:
        raise ResearchDeliverableError("paper_required", "生成汇报包至少需要一篇论文")
    if len(normalized) > MAX_PACK_PAPERS:
        raise ResearchDeliverableError("paper_limit_exceeded", f"一次最多支持 {MAX_PACK_PAPERS} 篇论文")
    cards = await _compose_cards(kb_id, normalized, current_user)
    resolved_model = _resolve_model_spec(model_spec)
    payload = await _call_model(
        resolved_model,
        PACK_SYSTEM_PROMPT,
        _pack_context(cards),
        stage="presentation",
    )
    outline = []
    for section in payload.get("outline") or []:
        if not isinstance(section, dict):
            continue
        title = str(section.get("section") or "").strip()
        if title:
            outline.append({"section": title, "bullets": _bullet_lines(section.get("bullets"))})
    if not outline:
        raise ResearchDeliverableError("presentation_invalid_schema", "汇报包缺少 outline 章节")
    comparison = payload.get("comparison") if isinstance(payload.get("comparison"), dict) else {}
    pack = {
        "title": str(payload.get("title") or "文献汇报").strip(),
        "audience": str(payload.get("audience") or "").strip(),
        "outline": outline,
        "comparison": {
            "columns": _bullet_lines(comparison.get("columns")),
            "rows": [_bullet_lines(row) for row in (comparison.get("rows") or []) if isinstance(row, (list, tuple))],
        },
        "talking_points": _bullet_lines(payload.get("talking_points")),
        "risks": _bullet_lines(payload.get("risks")),
        "papers": [{"paper_id": card["paper_id"], "title": card["title"]} for card in cards],
        "model": resolved_model,
        "generated_at": _now_text(),
    }
    diagrams = [
        {
            "paper_id": card["paper_id"],
            "title": card["title"],
            "steps": card["analysis"]["method_steps"],
            "svg": _render_flow_svg(
                f"方法流程：{(card['title'] or card['paper_id'])[:60]}",
                card["analysis"]["method_steps"],
            ),
        }
        for card in cards
        if card["analysis"]["method_steps"]
    ][:3]
    pack["diagrams"] = diagrams
    if len(diagrams) < sum(1 for card in cards if card["analysis"]["method_steps"]):
        pack["diagram_note"] = "仅展示前 3 篇有方法步骤的论文"
    return pack


def _markdown_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip() or "未说明"


def render_pack_markdown(pack: dict[str, Any]) -> str:
    lines = [f"# {pack.get('title') or '文献汇报'}", ""]
    if pack.get("audience"):
        lines.extend([f"- 听众：{pack['audience']}"])
    lines.extend([f"- 生成时间：{pack.get('generated_at', '')}", ""])
    lines.extend(["## 汇报大纲", ""])
    for index, section in enumerate(pack.get("outline") or [], start=1):
        lines.append(f"### {index}. {section['section']}")
        lines.extend([f"- {bullet}" for bullet in section.get("bullets") or []])
        lines.append("")
    columns = (pack.get("comparison") or {}).get("columns") or []
    rows = (pack.get("comparison") or {}).get("rows") or []
    if columns and rows:
        header_cells = " | ".join(columns)
        divider_cells = " | ".join(["---"] * len(columns))
        lines.extend(["## 对比表", "", f"| {header_cells} |", f"| {divider_cells} |"])
        for row in rows:
            cells = [_markdown_cell(cell) for cell in row]
            cells += [""] * (len(columns) - len(cells))
            lines.append("| " + " | ".join(cells[: len(columns)]) + " |")
        lines.append("")
    diagrams = pack.get("diagrams") or []
    if diagrams:
        lines.extend(["## 方法流程图", ""])
        for diagram in diagrams:
            lines.append(f"### {diagram.get('title') or diagram.get('paper_id')}")
            lines.extend(
                f"{index}. {step}" for index, step in enumerate(diagram.get("steps") or [], start=1)
            )
            lines.append("")
        lines.append("HTML 版包含对应的 SVG 流程图。")
        if pack.get("diagram_note"):
            lines.append(f"（{pack['diagram_note']}）")
        lines.append("")
    if pack.get("talking_points"):
        lines.extend(["## 讲稿要点", ""] + [f"- {item}" for item in pack["talking_points"]] + [""])
    if pack.get("risks"):
        lines.extend(["## 需要说明的局限", ""] + [f"- {item}" for item in pack["risks"]] + [""])
    if pack.get("papers"):
        lines.extend(["## 引用论文", ""] + [f"- {item['title']}" for item in pack["papers"]] + [""])
    return "\n".join(lines)


def render_pack_html(pack: dict[str, Any]) -> str:
    def esc(value: Any) -> str:
        return html.escape(str(value or ""))

    parts = [
        "<!doctype html>",
        '<html lang="zh-CN"><head><meta charset="utf-8" />',
        f"<title>{esc(pack.get('title') or '文献汇报')}</title>",
        "<style>",
        "body{font-family:-apple-system,Segoe UI,Microsoft YaHei,sans-serif;",
        "margin:40px auto;max-width:960px;color:#1f2328;line-height:1.7}",
        "h1{font-size:28px;margin-bottom:4px}",
        "h2{margin-top:28px;border-left:4px solid #046a82;padding-left:10px}",
        "h3{margin-bottom:6px}",
        ".meta{color:#6b7280;font-size:13px;margin-bottom:18px}",
        "table{border-collapse:collapse;width:100%;font-size:13px}",
        "th,td{border:1px solid #d0d7de;padding:6px 8px;text-align:left;vertical-align:top}",
        "th{background:#f3f6f8}",
        "ul{margin:6px 0 12px 22px}",
        ".note{color:#6b7280;font-size:12px;margin-top:32px}",
        "</style></head><body>",
        f"<h1>{esc(pack.get('title') or '文献汇报')}</h1>",
        (
            f'<p class="meta">听众：{esc(pack.get("audience") or "未指定")}'
            f" · 生成时间：{esc(pack.get('generated_at'))}</p>"
        ),
    ]
    for index, section in enumerate(pack.get("outline") or [], start=1):
        parts.append(f"<h2>{index}. {esc(section.get('section'))}</h2><ul>")
        parts.extend(f"<li>{esc(bullet)}</li>" for bullet in section.get("bullets") or [])
        parts.append("</ul>")
    columns = (pack.get("comparison") or {}).get("columns") or []
    rows = (pack.get("comparison") or {}).get("rows") or []
    if columns and rows:
        parts.append("<h2>对比表</h2><table><thead><tr>")
        parts.extend(f"<th>{esc(column)}</th>" for column in columns)
        parts.append("</tr></thead><tbody>")
        for row in rows:
            parts.append("<tr>")
            cells = list(row) + [""] * (len(columns) - len(row))
            parts.extend(f"<td>{esc(cell)}</td>" for cell in cells[: len(columns)])
            parts.append("</tr>")
        parts.append("</tbody></table>")
    diagrams = pack.get("diagrams") or []
    if diagrams:
        parts.append("<h2>方法流程图</h2>")
        for diagram in diagrams:
            parts.append(f"<h3>{esc(diagram.get('title') or diagram.get('paper_id'))}</h3>")
            parts.append(diagram.get("svg") or "")
        if pack.get("diagram_note"):
            parts.append(f'<p class="note">{esc(pack["diagram_note"])}</p>')
    if pack.get("talking_points"):
        parts.append("<h2>讲稿要点</h2><ul>")
        parts.extend(f"<li>{esc(item)}</li>" for item in pack["talking_points"])
        parts.append("</ul>")
    if pack.get("risks"):
        parts.append("<h2>需要说明的局限</h2><ul>")
        parts.extend(f"<li>{esc(item)}</li>" for item in pack["risks"])
        parts.append("</ul>")
    if pack.get("papers"):
        parts.append("<h2>引用论文</h2><ul>")
        parts.extend(f"<li>{esc(item['title'])}</li>" for item in pack["papers"])
        parts.append("</ul>")
    parts.append(f'<p class="note">由 ResearchCompass 生成，模型：{esc(pack.get("model"))}</p>')
    parts.append("</body></html>")
    return "".join(parts)


def export_pack(*, pack: dict[str, Any], export_format: str) -> tuple[str, bytes, str]:
    """把汇报包渲染为 MD 或 HTML，返回 (filename, content, media_type)。"""
    if export_format == "markdown":
        ext, media_type, content = "md", "text/markdown; charset=utf-8", render_pack_markdown(pack)
    elif export_format == "html":
        ext, media_type, content = "html", "text/html; charset=utf-8", render_pack_html(pack)
    else:
        raise ResearchDeliverableError("invalid_export_format", "汇报包只支持 markdown 或 html")
    filename = f"research_presentation_{utc_now_naive().strftime('%Y%m%d%H%M')}.{ext}"
    return filename, content.encode("utf-8"), media_type


DIAGRAM_KINDS = {"method": "flow", "topics": "hub", "citation": "hub"}


def _svg_escape(value: Any) -> str:
    from xml.sax.saxutils import escape

    return escape(str(value or ""))


def _svg_text(
    x: float,
    y: float,
    text: str,
    *,
    size: int = 13,
    fill: str = "#1f2328",
    weight: str = "400",
) -> str:
    return (
        f'<text x="{x}" y="{y}" text-anchor="middle" font-family="Microsoft YaHei, sans-serif" '
        f'font-size="{size}" font-weight="{weight}" fill="{fill}">{_svg_escape(text)}</text>'
    )


def _render_flow_svg(title: str, steps: list[str]) -> str:
    width, node_height, gap = 860, 56, 30
    height = 80 + len(steps) * (node_height + gap)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        _svg_text(width / 2, 38, title, size=18, weight="600"),
    ]
    top = 70
    for index, step in enumerate(steps):
        y = top + index * (node_height + gap)
        parts.append(
            f'<rect x="130" y="{y}" width="{width - 260}" height="{node_height}" rx="10" '
            'fill="#f3f8fa" stroke="#046a82"/>'
        )
        parts.append(_svg_text(width / 2, y + node_height / 2 + 5, f"{index + 1}. {step}"))
        if index < len(steps) - 1:
            arrow_y = y + node_height
            parts.append(
                f'<line x1="{width / 2}" y1="{arrow_y}" x2="{width / 2}" y2="{arrow_y + gap}" '
                'stroke="#046a82" marker-end="url(#arrow)"/>'
            )
    parts.append(
        '<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="5" refY="5" orient="auto">'
        '<path d="M0,0 L10,5 L0,10 z" fill="#046a82"/></marker></defs>'
    )
    parts.append("</svg>")
    return "".join(parts)


def _render_hub_svg(title: str, center: str, spokes: list[str]) -> str:
    import math

    width, height, radius = 860, 520, 180
    cx, cy = width / 2, height / 2 + 20
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        _svg_text(width / 2, 36, title, size=18, weight="600"),
    ]
    count = max(len(spokes), 1)
    for index, spoke in enumerate(spokes):
        angle = -math.pi / 2 + 2 * math.pi * index / count
        x = cx + radius * math.cos(angle)
        y = cy + radius * math.sin(angle)
        parts.append(f'<line x1="{cx:.1f}" y1="{cy:.1f}" x2="{x:.1f}" y2="{y:.1f}" stroke="#9fb3bd"/>')
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="46" fill="#f3f8fa" stroke="#046a82"/>')
        parts.append(_svg_text(round(x, 1), round(y + 4, 1), spoke[:14], size=11))
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="70" fill="#046a82"/>')
    parts.append(_svg_text(cx, cy + 5, center[:18], size=12, fill="#ffffff"))
    parts.append("</svg>")
    return "".join(parts)


async def generate_structure_diagram(
    *, kb_id: str, paper_id: str, current_user: User, kind: str
) -> tuple[str, bytes, str]:
    """根据论文分析结果或关键词生成结构图（SVG）：方法流程或主题图。"""
    await _ensure_access(current_user, kb_id)
    if kind not in DIAGRAM_KINDS:
        raise ResearchDeliverableError("invalid_diagram_kind", "结构图只支持 method 或 topics")
    paper = await AcademicPaperRepository().get_by_paper_id(kb_id=kb_id, paper_id=paper_id)
    if paper is None:
        raise ResearchDeliverableError("paper_not_found", "论文不存在")
    title = f"{(paper.title or paper_id)[:60]}"
    if kind == "method":
        from yuxi.services.academic_paper_analysis_service import get_latest_paper_analysis

        analysis = await get_latest_paper_analysis(kb_id=kb_id, paper_id=paper_id, current_user=current_user)
        stages = _stage_payload(analysis)
        methodology = stages.get("methodology")
        if not isinstance(methodology, dict):
            single = stages.get("single_agent")
            methodology = single.get("methodology") if isinstance(single, dict) else None
        steps = _bullet_lines((methodology or {}).get("method_steps"))
        if not steps:
            structure = _structure_of(stages)
            if structure.get("method"):
                steps = [str(structure["method"])]
        if not steps:
            raise ResearchDeliverableError("diagram_no_data", "还没有可用的方法步骤，请先生成论文分析")
        svg = _render_flow_svg(f"方法流程：{title}", steps[:12])
    elif kind == "topics":
        keywords = _string_list(paper.keywords)
        if not keywords:
            raise ResearchDeliverableError("diagram_no_data", "该论文没有可用的关键词，无法生成主题图")
        svg = _render_hub_svg(f"主题图：{title}", title, keywords[:8])
    else:
        nodes, edges = await _load_graph_snapshot(kb_id)
        papers, indegree, outdegree = _citation_metrics(nodes, edges)
        if not papers:
            raise ResearchDeliverableError("graph_empty", "引用图谱为空，请先同步引用图谱")
        ranked = sorted(
            papers.values(), key=lambda item: (indegree[str(item["id"])], outdegree[str(item["id"])]), reverse=True
        )
        top = ranked[0]
        cites, cited_by = _citation_neighbors(str(top["id"]), papers, edges, limit=4)
        spokes = list(dict.fromkeys([*cites, *cited_by]))[:8]
        if not spokes:
            spokes = [str(item.get("title") or item.get("id")) for item in ranked[1:6]]
        svg = _render_hub_svg("引用脉络", str(top.get("title") or top["id"]), spokes)
    filename = f"research_structure_{kind}_{paper_id}.svg"
    return filename, svg.encode("utf-8"), "image/svg+xml; charset=utf-8"


EXPORT_DATA_KINDS = {"eval_run", "eval_experiment", "trends", "opportunities"}


def _flatten_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)] or [{"value": payload}]
    if not isinstance(payload, dict):
        return [{"value": payload}]
    for key in ("items", "responses", "keywords", "variants", "runs", "papers", "results"):
        value = payload.get(key)
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return value
    for value in payload.values():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return value
    return [payload]


def _render_rows_csv(rows: list[dict[str, Any]]) -> str:
    import csv
    import io

    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(columns)
    for row in rows:
        cells = []
        for key in columns:
            value = row.get(key)
            if isinstance(value, (dict, list)):
                cells.append(json.dumps(value, ensure_ascii=False))
            else:
                cells.append("" if value is None else str(value))
        writer.writerow(cells)
    return buffer.getvalue()


async def export_research_data(
    *,
    kb_id: str,
    current_user: User,
    kind: str,
    target_id: str | None,
    export_format: str,
    year_from: int | None = None,
    year_to: int | None = None,
) -> tuple[str, bytes, str]:
    """导出评估运行、消融实验、趋势或机会统计为 JSON/CSV，并附带数据指纹。"""
    from yuxi.utils.datetime_utils import utc_now_naive

    await _ensure_access(current_user, kb_id)
    if kind not in EXPORT_DATA_KINDS:
        raise ResearchDeliverableError(
            "invalid_data_kind", "只支持 eval_run / eval_experiment / trends / opportunities"
        )
    if export_format not in {"json", "csv"}:
        raise ResearchDeliverableError("invalid_export_format", "数据导出只支持 json 或 csv")
    if kind in {"eval_run", "eval_experiment"}:
        if str(getattr(current_user, "role", "")) not in {"admin", "superadmin"}:
            raise ResearchDeliverableError("forbidden", "评估数据导出仅管理员可用")
    if kind in {"eval_run", "eval_experiment"} and not (target_id or "").strip():
        raise ResearchDeliverableError("target_required", "评估数据导出需要提供 target_id")

    if kind == "eval_run":
        from yuxi.knowledge.eval.service import EvaluationService

        payload = await EvaluationService().get_run_results(kb_id, str(target_id).strip(), page=1, page_size=100)
        fingerprint = {"kind": kind, "run_id": target_id, "kb_id": kb_id}
    elif kind == "eval_experiment":
        from yuxi.knowledge.eval.service import EvaluationService

        payload = await EvaluationService().get_experiment(kb_id, str(target_id).strip())
        fingerprint = {"kind": kind, "experiment_id": target_id, "kb_id": kb_id}
    elif kind == "trends":
        from yuxi.services.academic_trend_service import get_academic_trends

        payload = await get_academic_trends(
            kb_id=kb_id,
            current_user=current_user,
            year_from=year_from,
            year_to=year_to,
            offset=0,
            limit=200,
            top_keywords=20,
        )
        fingerprint = {"kind": kind, "kb_id": kb_id, "year_from": year_from, "year_to": year_to}
    else:
        from yuxi.services.academic_opportunity_service import get_academic_opportunities

        payload = await get_academic_opportunities(
            kb_id=kb_id, current_user=current_user, year_from=year_from, year_to=year_to, limit=50
        )
        fingerprint = {"kind": kind, "kb_id": kb_id, "year_from": year_from, "year_to": year_to}

    meta = {
        "generated_at": utc_now_naive().strftime("%Y-%m-%d %H:%M"),
        "fingerprint": fingerprint,
    }
    stamp = utc_now_naive().strftime("%Y%m%d%H%M")
    if export_format == "json":
        body = json.dumps({**meta, "data": payload}, ensure_ascii=False, indent=2)
        media_type = "application/json; charset=utf-8"
        ext = "json"
    else:
        rows = _flatten_rows(payload)
        header = f"# generated_at={meta['generated_at']} fingerprint={json.dumps(fingerprint, ensure_ascii=False)}\n"
        body = header + _render_rows_csv(rows)
        media_type = "text/csv; charset=utf-8"
        ext = "csv"
    filename = f"research_{kind}_{kb_id}_{stamp}.{ext}"
    return filename, body.encode("utf-8"), media_type


COMPARISON_FIELDS = (
    ("title", "论文"),
    ("year", "年份"),
    ("venue", "来源"),
    ("problem", "研究问题"),
    ("method", "方法"),
    ("datasets", "数据集"),
    ("results", "结论"),
    ("limitations", "局限"),
)


def _comparison_value(card: dict[str, Any], key: str) -> str:
    if key == "title":
        return str(card.get("title") or card.get("paper_id") or "")
    if key == "year":
        return str(card.get("year") or "未说明")
    if key == "venue":
        return str(card.get("venue") or "未说明")
    analysis = card.get("analysis") or {}
    if key in {"datasets", "results", "limitations"}:
        values = analysis.get(key) or []
        return "；".join(str(item) for item in values) if values else "证据不足"
    return str(analysis.get(key) or "未说明")


async def export_comparison_matrix(
    *, kb_id: str, current_user: User, paper_ids: list[str], export_format: str
) -> tuple[str, bytes, str]:
    """把多篇论文按统一字段渲染为对比矩阵，支持 Markdown 与 CSV。"""
    import csv
    import io

    from yuxi.utils.datetime_utils import utc_now_naive

    if export_format not in {"markdown", "csv"}:
        raise ResearchDeliverableError("invalid_export_format", "对比矩阵只支持 markdown 或 csv")
    normalized = list(dict.fromkeys(item.strip() for item in paper_ids if item.strip()))
    if len(normalized) < 2:
        raise ResearchDeliverableError("matrix_requires_two_papers", "对比矩阵至少需要两篇论文")
    if len(normalized) > 20:
        raise ResearchDeliverableError("paper_limit_exceeded", "对比矩阵一次最多支持 20 篇论文")
    cards = await _compose_cards(kb_id, normalized, current_user)
    if len(cards) < 2:
        raise ResearchDeliverableError("matrix_requires_two_papers", "对比矩阵至少需要两篇论文")
    header = [label for _, label in COMPARISON_FIELDS]
    rows = [[_comparison_value(card, key) for key, _ in COMPARISON_FIELDS] for card in cards]
    stamp = utc_now_naive().strftime("%Y%m%d%H%M")
    if export_format == "markdown":
        lines = [
            "# 文献对比矩阵",
            "",
            f"- 论文数：{len(cards)}",
            f"- 生成时间：{utc_now_naive().strftime('%Y-%m-%d %H:%M')}",
            "- 说明：单元格内容来自论文元数据与最近一次分析；“证据不足”表示尚未生成分析或原文未说明。",
            "",
            "| " + " | ".join(header) + " |",
            "| " + " | ".join(["---"] * len(header)) + " |",
        ]
        for row in rows:
            lines.append("| " + " | ".join(_markdown_cell(cell) for cell in row) + " |")
        body = "\n".join(lines)
        media_type, ext = "text/markdown; charset=utf-8", "md"
    else:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(header)
        for row in rows:
            writer.writerow([("'" + cell) if cell[:1] in {"=", "+", "-", "@"} else cell for cell in row])
        body = buffer.getvalue()
        media_type, ext = "text/csv; charset=utf-8", "csv"
    filename = f"research_comparison_{kb_id}_{stamp}.{ext}"
    return filename, body.encode("utf-8"), media_type


async def _load_graph_snapshot(kb_id: str, limit: int = 500) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from yuxi.knowledge.graphs.academic_graph_service import AcademicGraphService

    network = await AcademicGraphService().get_network(kb_id=kb_id, center_paper_id=None, depth=1, limit=limit)
    nodes = [item for item in network.get("nodes") or [] if isinstance(item, dict)]
    edges = [item for item in network.get("edges") or [] if isinstance(item, dict)]
    return nodes, edges


def _citation_metrics(
    nodes: list[dict[str, Any]], edges: list[dict[str, Any]]
) -> tuple[dict[str, dict[str, Any]], dict[str, int], dict[str, int]]:
    papers = {str(n.get("id")): n for n in nodes if n.get("type") == "paper" and n.get("id")}
    indegree = {pid: 0 for pid in papers}
    outdegree = {pid: 0 for pid in papers}
    for edge in edges:
        if edge.get("type") != "CITES":
            continue
        source, target = str(edge.get("source") or ""), str(edge.get("target") or "")
        if source in papers and target in papers:
            outdegree[source] += 1
            indegree[target] += 1
    return papers, indegree, outdegree


def _citation_neighbors(
    paper_id: str, papers: dict[str, dict[str, Any]], edges: list[dict[str, Any]], limit: int = 5
) -> tuple[list[str], list[str]]:
    cites, cited_by = [], []
    for edge in edges:
        if edge.get("type") != "CITES":
            continue
        source, target = str(edge.get("source") or ""), str(edge.get("target") or "")
        if source == paper_id and target in papers:
            cites.append(str(papers[target].get("title") or target))
        elif target == paper_id and source in papers:
            cited_by.append(str(papers[source].get("title") or source))
    return cites[:limit], cited_by[:limit]


async def generate_citation_insight(*, kb_id: str, current_user: User) -> dict[str, Any]:
    """基于当前引用图谱快照生成确定性洞察：枢纽论文、引用谱系与孤立节点。"""
    from yuxi.utils.datetime_utils import utc_now_naive

    await _ensure_access(current_user, kb_id)
    nodes, edges = await _load_graph_snapshot(kb_id)
    if not nodes:
        raise ResearchDeliverableError("graph_empty", "引用图谱为空，请先同步引用图谱")
    papers, indegree, outdegree = _citation_metrics(nodes, edges)
    ranked = sorted(papers.values(), key=lambda p: (indegree[str(p["id"])], outdegree[str(p["id"])]), reverse=True)
    hubs = ranked[:5]
    isolates = [p for p in papers.values() if indegree[str(p["id"])] == 0 and outdegree[str(p["id"])] == 0][:5]
    citation_edges = sum(1 for e in edges if e.get("type") == "CITES")
    lines = [
        "# 引用网络洞察报告",
        "",
        f"- 生成时间：{utc_now_naive().strftime('%Y-%m-%d %H:%M')}",
        f"- 图谱快照：{len(papers)} 篇论文、{citation_edges} 条引用边（导出上限 500 个节点）",
        "",
        "## 一、枢纽论文",
        "",
    ]
    if hubs:
        for index, paper in enumerate(hubs, start=1):
            pid = str(paper["id"])
            lines.append(f"{index}. {paper.get('title') or pid}（被引 {indegree[pid]} 次 / 引用 {outdegree[pid]} 篇）")
            lines.append(f"   - 依据：图谱节点 {pid} 的 CITES 入边与出边计数")
    else:
        lines.append("当前快照没有可用的论文节点。")
    lines.extend(["", "## 二、研究谱系线索", ""])
    if hubs:
        top = str(hubs[0]["id"])
        cites, cited_by = _citation_neighbors(top, papers, edges)
        lines.append(f"以枢纽论文「{papers[top].get('title') or top}」为中心：")
        lines.append("- 它引用的论文：" + ("；".join(cites) if cites else "快照内无记录"))
        lines.append("- 引用它的论文：" + ("；".join(cited_by) if cited_by else "快照内无记录"))
        lines.append("- 依据：图谱 CITES 边的直接邻接关系，未做跨快照推断")
    else:
        lines.append("缺少枢纽论文，无法展开谱系。")
    lines.extend(["", "## 三、引用断层与孤岛", ""])
    if isolates:
        for paper in isolates:
            lines.append(f"- {paper.get('title') or paper.get('id')}：快照内既无入边也无出边")
        lines.append("- 说明：孤岛通常意味着该论文尚未同步引用关系，或确实未被当前语料引用；需结合同步运行记录判断。")
    else:
        lines.append("当前快照未发现孤立论文节点。")
    lines.extend(
        [
            "",
            "## 数据边界",
            "",
            "- 本报告只统计当前知识库引用图谱快照，不代表领域完整引用网络。",
            "- 边计数来自 Semantic Scholar 同步结果；缺失边会在孤岛一节体现，不做补齐或推测。",
        ]
    )
    return {
        "markdown": "\n".join(lines),
        "paper_count": len(papers),
        "citation_edge_count": citation_edges,
        "hub_count": len(hubs),
        "isolate_count": len(isolates),
        "generated_at": utc_now_naive().strftime("%Y-%m-%d %H:%M"),
    }


def _topic_items(result: dict[str, Any]) -> list[dict[str, str]]:
    gaps = result.get("research_gaps") or result.get("gaps") or {}
    items: list[dict[str, str]] = []
    if isinstance(gaps, dict):
        for gap in gaps.get("gaps") or []:
            if isinstance(gap, dict) and str(gap.get("claim") or "").strip():
                items.append(
                    {
                        "topic": str(gap["claim"]).strip(),
                        "basis": str(gap.get("basis") or "").strip(),
                        "confidence": str(gap.get("confidence") or "").strip(),
                        "source": "研究空白",
                    }
                )
        for direction in gaps.get("future_directions") or []:
            if isinstance(direction, dict):
                text = str(direction.get("direction") or direction.get("title") or direction.get("claim") or "").strip()
                basis = str(direction.get("basis") or "综述给出的后续方向").strip()
                confidence = str(direction.get("confidence") or "").strip()
            else:
                text = str(direction).strip()
                basis = "综述给出的后续方向"
                confidence = ""
            if text and text.lower() not in {"none", "null"}:
                items.append(
                    {
                        "topic": text,
                        "basis": basis,
                        "confidence": confidence,
                        "source": "后续方向",
                    }
                )
    unsupported = result.get("unsupported_claims") or result.get("unsupported") or []
    if isinstance(unsupported, list):
        for claim in unsupported:
            if isinstance(claim, dict):
                text = str(
                    claim.get("claim") or claim.get("statement") or claim.get("text") or claim.get("title") or ""
                ).strip()
            else:
                text = str(claim).strip()
            if text and text.lower() not in {"none", "null"}:
                items.append(
                    {
                        "topic": text,
                        "basis": "综述中未被证据支持的结论，需补文献后再定题",
                        "confidence": "",
                        "source": "未支持结论",
                    }
                )
    seen: set[str] = set()
    unique: list[dict[str, str]] = []
    for item in items:
        if item["topic"] in seen:
            continue
        seen.add(item["topic"])
        unique.append(item)
    return unique


def render_topic_markdown(run_id: str, items: list[dict[str, str]], generated_at: str) -> str:
    lines = [
        "# 选题清单",
        "",
        f"- 来源综述：{run_id}",
        f"- 生成时间：{generated_at}",
        f"- 选题数：{len(items)}",
        "- 说明：选题来自综述的研究空白、后续方向与未支持结论；未支持结论需先补文献，不建议直接开题。",
        "",
    ]
    for index, item in enumerate(items, start=1):
        suffix = f"（置信度：{item['confidence']}）" if item["confidence"] else ""
        lines.append(f"{index}. {item['topic']}{suffix}")
        lines.append(f"   - 来源：{item['source']}")
        if item["basis"]:
            lines.append(f"   - 依据：{item['basis']}")
    return "\n".join(lines)


async def build_topic_list(*, run_id: str, current_user: User) -> dict[str, Any]:
    """从一次证据综述运行提取可追踪的选题清单。"""
    from yuxi.services.research_synthesis_service import get_research_synthesis
    from yuxi.utils.datetime_utils import utc_now_naive

    run = await get_research_synthesis(run_id=run_id, current_user=current_user)
    if str(run.get("status")) != "success":
        raise ResearchDeliverableError("synthesis_not_success", "只有验证完成的证据综述才能转选题")
    result = run.get("result") if isinstance(run.get("result"), dict) else {}
    items = _topic_items(result)
    if not items:
        raise ResearchDeliverableError("topic_no_data", "本次综述没有可转成选题的空白或方向")
    generated_at = utc_now_naive().strftime("%Y-%m-%d %H:%M")
    return {
        "run_id": run_id,
        "items": items,
        "generated_at": generated_at,
        "markdown": render_topic_markdown(run_id, items, generated_at),
    }


async def create_tasks_from_topics(
    *, run_id: str, project_id: str, current_user: User, limit: int = 10
) -> dict[str, Any]:
    """把综述选题批量建为当前项目的待办任务，标题与依据一一对应。"""
    from yuxi.services.research_project_plan_service import create_project_task

    topics = await build_topic_list(run_id=run_id, current_user=current_user)
    created = []
    for item in topics["items"][:limit]:
        description = f"来源：证据综述 {run_id} · {item['source']}"
        if item["basis"]:
            description += f"\n依据：{item['basis']}"
        task = await create_project_task(
            project_id=project_id,
            current_user=current_user,
            title=item["topic"][:200],
            description=description,
            status="todo",
            priority="medium",
            due_date=None,
            milestone_id=None,
        )
        created.append({"task_id": task.get("task_id"), "title": task.get("title")})
    return {"created": len(created), "tasks": created, "source_run_id": run_id}


def _duplicate_keys(item: Any) -> list[str]:
    """返回条目的所有可用去重标识；DOI 与归一化标题可同时存在。"""

    def _value(field: str) -> Any:
        return item.get(field) if isinstance(item, dict) else getattr(item, field, None)

    keys: list[str] = []
    doi = str(_value("doi") or "").strip().lower()
    if doi:
        keys.append(f"doi:{doi}")
    title = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(_value("title") or "").lower())
    if title:
        keys.append(f"title:{title[:80]}")
    return keys


def _group_duplicate_items(items: list[Any]) -> list[tuple[str, list[Any]]]:
    """按 DOI/标题连通关系分组，避免有 DOI 的条目与同标题无 DOI 条目漏配。"""
    parent = list(range(len(items)))

    def _find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def _union(left: int, right: int) -> None:
        left_root, right_root = _find(left), _find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    key_owner: dict[str, int] = {}
    keys_by_index: list[list[str]] = []
    for index, item in enumerate(items):
        keys = _duplicate_keys(item)
        keys_by_index.append(keys)
        for key in keys:
            if key in key_owner:
                _union(index, key_owner[key])
            else:
                key_owner[key] = index

    grouped: dict[int, list[int]] = {}
    for index, keys in enumerate(keys_by_index):
        if keys:
            grouped.setdefault(_find(index), []).append(index)

    result: list[tuple[str, list[Any]]] = []
    for indices in grouped.values():
        if len(indices) < 2:
            continue
        group_keys = {key for index in indices for key in keys_by_index[index]}
        representative_key = sorted(group_keys, key=lambda key: (not key.startswith("doi:"), key))[0]
        result.append((representative_key, [items[index] for index in indices]))
    return result


async def find_cross_library_duplicates(*, current_user: User, max_libraries: int = 10) -> dict[str, Any]:
    """在当前用户可访问的多个知识库之间识别重复论文，只报告不合并。"""
    from yuxi.knowledge.runtime import knowledge_base as kb_runtime
    from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository

    user_info = {
        "uid": str(current_user.uid),
        "role": current_user.role,
        "department_id": current_user.department_id,
    }
    libraries = []
    for kb in await KnowledgeBaseRepository().get_all():
        if str(getattr(kb, "kb_type", "milvus") or "").lower() != "milvus":
            continue
        if not await kb_runtime.check_accessible(user_info, kb.kb_id):
            continue
        libraries.append(kb)
        if len(libraries) >= max_libraries:
            break
    if len(libraries) < 2:
        raise ResearchDeliverableError("cross_library_insufficient", "跨库查重至少需要两个可访问的论文知识库")

    collected: list[dict[str, Any]] = []
    for kb in libraries:
        async for batch in AcademicPaperRepository().iter_export_batches(kb_id=str(kb.kb_id)):
            for paper in batch:
                collected.append(
                    {
                        "kb_id": str(kb.kb_id),
                        "kb_name": str(kb.name or kb.kb_id),
                        "paper_id": str(paper.paper_id),
                        "title": str(paper.title or ""),
                        "publication_year": paper.publication_year,
                        "doi": str(paper.doi or ""),
                    }
                )

    duplicates = []
    for key, items in _group_duplicate_items(collected):
        library_ids = {item["kb_id"] for item in items}
        if len(library_ids) < 2:
            continue
        duplicates.append(
            {
                "key": key,
                "paper_count": len(items),
                "library_count": len(library_ids),
                "papers": sorted(items, key=lambda item: (item["kb_id"], item["paper_id"])),
            }
        )
    duplicates.sort(key=lambda item: (item["library_count"], item["paper_count"]), reverse=True)
    return {
        "library_count": len(libraries),
        "libraries": [{"kb_id": str(kb.kb_id), "name": str(kb.name or "")} for kb in libraries],
        "duplicate_group_count": len(duplicates),
        "duplicate_paper_count": sum(item["paper_count"] for item in duplicates),
        "groups": duplicates,
        "note": (
            "只报告跨库重复，不自动合并；跨库合并需先确定保留库与原文、证据和项目资产的迁移策略，当前仅库内合并可用"
        ),
    }


async def find_duplicate_papers(*, kb_id: str, current_user: User) -> dict[str, Any]:
    """在当前知识库内按 DOI（优先）或归一化标题检测重复论文，只读不改数据。"""
    await _ensure_access(current_user, kb_id)
    repo = AcademicPaperRepository()
    papers: list[Any] = []
    async for batch in repo.iter_export_batches(kb_id=kb_id):
        papers.extend(batch)
    duplicates = []
    for key, items in _group_duplicate_items(papers):
        if len(items) < 2:
            continue
        def _rank(item: Any) -> tuple[bool, bool, int]:
            doi = str(getattr(item, "doi", "") or "").strip()
            file_id = str(getattr(item, "file_id", "") or "").strip()
            year = getattr(item, "publication_year", None)
            try:
                year_value = int(year) if year is not None else 10**6
            except (TypeError, ValueError):
                year_value = 10**6
            return (bool(file_id), bool(doi), -year_value)

        ranked = sorted(items, key=_rank, reverse=True)
        keep = ranked[0]
        duplicates.append(
            {
                "key": key,
                "count": len(items),
                "recommended_paper_id": str(getattr(keep, "paper_id", "")),
                "recommendation": "建议优先保留有全文、有 DOI 且年份最早的条目；是否合并需人工确认，不自动处理",
                "merge_preview": {
                    "keep": {
                        "paper_id": str(getattr(keep, "paper_id", "")),
                        "title": str(getattr(keep, "title", "") or ""),
                    },
                    "move": [
                        {
                            "paper_id": str(getattr(item, "paper_id", "")),
                            "title": str(getattr(item, "title", "") or ""),
                        }
                        for item in items
                        if str(getattr(item, "paper_id", "")) != str(getattr(keep, "paper_id", ""))
                    ],
                    "executed": False,
                },
                "papers": [
                    {
                        "paper_id": str(getattr(item, "paper_id", "")),
                        "title": str(getattr(item, "title", "") or ""),
                        "publication_year": getattr(item, "publication_year", None),
                        "file_id": str(getattr(item, "file_id", "") or ""),
                    }
                    for item in items
                ],
            }
        )
    duplicates.sort(key=lambda item: item["count"], reverse=True)
    return {
        "kb_id": kb_id,
        "paper_count": len(papers),
        "duplicate_group_count": len(duplicates),
        "duplicate_paper_count": sum(item["count"] for item in duplicates),
        "groups": duplicates,
    }


async def export_kb_manifest(*, kb_id: str, current_user: User) -> dict[str, Any]:
    """导出知识库迁移清单：论文元数据 + 图谱统计，不含原文与向量。"""
    from yuxi.repositories.academic_graph_repository import AcademicGraphRepository

    await _ensure_access(current_user, kb_id)
    papers: list[dict[str, Any]] = []
    async for batch in AcademicPaperRepository().iter_export_batches(kb_id=kb_id):
        for paper in batch:
            papers.append(
                {
                    "paper_id": str(getattr(paper, "paper_id", "")),
                    "file_id": str(getattr(paper, "file_id", "") or ""),
                    "title": str(getattr(paper, "title", "") or ""),
                    "publication_year": getattr(paper, "publication_year", None),
                    "doi": str(getattr(paper, "doi", "") or ""),
                    "venue": str(getattr(paper, "venue", "") or ""),
                }
            )
    graph_counts = await AcademicGraphRepository().counts(kb_id)
    return {
        "kb_id": kb_id,
        "generated_at": utc_now_naive().strftime("%Y-%m-%d %H:%M"),
        "paper_count": len(papers),
        "papers": papers,
        "graph_counts": graph_counts,
        "note": "本清单只含元数据与图谱统计；原文文件、向量与图数据由备份链路（Postgres + docker volumes）负责。",
    }


BUNDLE_README = """# ResearchCompass 知识库迁移包

本包包含：

- `manifest.json`：论文元数据与图谱统计（与“导出知识库清单”一致）
- `papers.csv`：论文元数据表
- `files.csv`：知识库文件清单（不含二进制）
- `graph/citation_graph.csv`：引用图谱快照（最多 500 个节点）

恢复方式：

1. 原文文件、向量与图数据由备份链路（Postgres + docker volumes）恢复；
2. 在新环境导入后用论文库“校验清单”上传 `manifest.json`，核对缺失与新增；
3. 本包不包含 PDF 原文与向量，不能单独完成整库恢复。
"""


def _bundle_csv(header: list[str], rows: list[list[Any]]) -> bytes:
    """渲染 UTF-8 BOM CSV，并对公式样式单元格加前导单引号。"""
    import csv
    from io import StringIO

    buffer = StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        normalized = [("'" + str(cell)) if str(cell or "")[:1] in {"=", "+", "-", "@"} else cell for cell in row]
        writer.writerow(normalized)
    return buffer.getvalue().encode("utf-8-sig")


async def export_kb_bundle(*, kb_id: str, current_user: User) -> tuple[str, bytes, str]:
    """打包知识库迁移文件：清单、论文表、文件清单与图谱快照（不含二进制）。"""
    import zipfile
    from io import BytesIO

    from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
    from yuxi.services.academic_graph_sync_service import export_academic_graph

    await _ensure_access(current_user, kb_id)
    manifest = await export_kb_manifest(kb_id=kb_id, current_user=current_user)
    papers = manifest["papers"]
    files = [record for record in await KnowledgeFileRepository().list_by_kb_id(kb_id) if not record.is_folder]

    try:
        _, graph_bytes, _ = await export_academic_graph(kb_id=kb_id, current_user=current_user, export_format="csv")
        graph_csv = graph_bytes.decode("utf-8")
    except Exception as exc:  # noqa: BLE001 - 图谱为空不应阻断迁移包导出
        if getattr(exc, "error_type", "") != "graph_empty":
            raise
        graph_csv = "notice\n图谱尚未同步，本包只含元数据与文件清单\n"

    paper_rows = [
        [
            paper["paper_id"],
            paper["title"],
            paper["publication_year"],
            paper["venue"],
            paper["doi"],
            paper["file_id"],
        ]
        for paper in papers
    ]
    file_rows = [
        [
            str(record.file_id),
            str(record.filename or ""),
            str(record.original_filename or ""),
            str(record.file_type or ""),
            str(record.status or ""),
            record.file_size,
            int(record.chunk_count or 0),
            "yes" if (record.minio_url or record.path) else "no",
            "yes" if record.markdown_file else "no",
        ]
        for record in files
    ]

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr(
            "papers.csv",
            _bundle_csv(["paper_id", "title", "publication_year", "venue", "doi", "file_id"], paper_rows),
        )
        archive.writestr(
            "files.csv",
            _bundle_csv(
                [
                    "file_id",
                    "filename",
                    "original_filename",
                    "file_type",
                    "status",
                    "file_size",
                    "chunk_count",
                    "original_available",
                    "parsed_available",
                ],
                file_rows,
            ),
        )
        archive.writestr("graph/citation_graph.csv", graph_csv)
        archive.writestr("README.md", BUNDLE_README)
    filename = f"research_bundle_{kb_id}_{utc_now_naive().strftime('%Y%m%d%H%M')}.zip"
    return filename, buffer.getvalue(), "application/zip"


async def merge_duplicate_group(*, kb_id: str, key: str, current_user: User) -> dict[str, Any]:
    """按重复分组做归档式合并：保留建议条目，其余写归档时间，可恢复。"""
    await _ensure_access(current_user, kb_id, write=True)
    result = await find_duplicate_papers(kb_id=kb_id, current_user=current_user)
    group = next((item for item in result["groups"] if item["key"] == key), None)
    if group is None:
        raise ResearchDeliverableError("duplicate_group_not_found", "该重复分组不存在或已被处理")
    keep_id = group["recommended_paper_id"]
    remove_ids = [paper["paper_id"] for paper in group["papers"] if paper["paper_id"] != keep_id]
    archived = await AcademicPaperRepository().archive_papers(kb_id=kb_id, paper_ids=remove_ids)
    return {
        "key": key,
        "kept_paper_id": keep_id,
        "archived_paper_ids": archived,
        "message": (
            "重复条目已归档（独立归档时间，不占用 metadata_status），列表、检索、导出与统计不再包含这些论文；"
            "原文文件与向量未删除，可调用恢复接口撤销"
        ),
    }


async def restore_archived_papers(*, kb_id: str, paper_ids: list[str], current_user: User) -> dict[str, Any]:
    """恢复归档论文，使其恢复归档前的元数据状态并重新参与检索与导出。"""
    await _ensure_access(current_user, kb_id, write=True)
    restored = await AcademicPaperRepository().restore_papers(kb_id=kb_id, paper_ids=paper_ids)
    return {"restored_paper_ids": restored, "count": len(restored)}


async def list_archived_papers(*, kb_id: str, current_user: User, limit: int = 100) -> dict[str, Any]:
    """列出已归档论文，供合并后核对可见性与按需恢复。"""
    await _ensure_access(current_user, kb_id)
    papers = await AcademicPaperRepository().list_archived_papers(kb_id=kb_id, limit=limit)
    return {
        "count": len(papers),
        "items": [
            {
                "paper_id": str(paper.paper_id),
                "title": str(paper.title or ""),
                "publication_year": paper.publication_year,
                "doi": str(paper.doi or ""),
                "metadata_status": str(paper.metadata_status or ""),
                "archived_at": paper.archived_at.isoformat() if paper.archived_at else None,
            }
            for paper in papers
        ],
    }


MAX_MANIFEST_ENTRIES = 20000
MANIFEST_DETAIL_LIMIT = 50


def _manifest_doi(value: Any) -> str:
    doi = str(value or "").strip().casefold()
    return re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi)


async def validate_kb_manifest(*, kb_id: str, manifest: Any, current_user: User) -> dict[str, Any]:
    """把迁移清单与当前库论文比对，报告缺失与新增条目，用于换机/恢复后的校验。"""
    await _ensure_access(current_user, kb_id)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("papers"), list):
        raise ResearchDeliverableError("manifest_invalid", "迁移清单缺少 papers 数组")
    entries = [item for item in manifest["papers"] if isinstance(item, dict)]
    if not entries:
        raise ResearchDeliverableError("manifest_empty", "迁移清单没有论文条目")
    if len(entries) > MAX_MANIFEST_ENTRIES:
        raise ResearchDeliverableError("manifest_too_large", f"迁移清单最多支持 {MAX_MANIFEST_ENTRIES} 篇论文")

    current_by_id: dict[str, Any] = {}
    current_by_doi: dict[str, Any] = {}
    async for batch in AcademicPaperRepository().iter_export_batches(kb_id=kb_id):
        for paper in batch:
            current_by_id[str(paper.paper_id)] = paper
            doi = _manifest_doi(paper.doi)
            if doi:
                current_by_doi[doi] = paper

    matched_ids: set[str] = set()
    missing: list[dict[str, Any]] = []
    for entry in entries:
        paper_id = str(entry.get("paper_id") or "")
        doi = _manifest_doi(entry.get("doi"))
        paper = current_by_id.get(paper_id) or (current_by_doi.get(doi) if doi else None)
        if paper is None:
            missing.append(
                {
                    "paper_id": paper_id,
                    "title": str(entry.get("title") or ""),
                    "doi": str(entry.get("doi") or ""),
                }
            )
            continue
        matched_ids.add(str(paper.paper_id))

    extras = [paper for paper_id, paper in current_by_id.items() if paper_id not in matched_ids]
    return {
        "kb_id": kb_id,
        "manifest_paper_count": len(entries),
        "library_paper_count": len(current_by_id),
        "matched_count": len(matched_ids),
        "missing_count": len(missing),
        "extra_count": len(extras),
        "missing": missing[:MANIFEST_DETAIL_LIMIT],
        "extra": [
            {"paper_id": str(paper.paper_id), "title": str(paper.title or "")}
            for paper in extras[:MANIFEST_DETAIL_LIMIT]
        ],
        "consistent": not missing,
        "note": (
            "校验按 paper_id 优先、DOI 兜底匹配；“新增”表示当前库多出的论文，迁移后新增文献属正常情况；"
            "本校验只对比元数据，不校验原文文件、向量与图谱。"
        ),
    }
