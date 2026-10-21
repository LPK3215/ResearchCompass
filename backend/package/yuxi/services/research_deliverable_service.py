"""科研交付物服务：把论文元数据、分析结果与项目资料渲染为可交付文件。

本模块只做“已有数据 -> 文件”的确定性渲染，以及需要模型参与的两类生成（分层摘要、汇报包）。
模型调用沿用论文分析服务的约定：单次调用、严格 JSON、失败显式报错。
"""

from __future__ import annotations

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
    model = select_model(model_spec=model_spec, model_params={"temperature": 0})
    try:
        response = await model.call(
            [{"role": "system", "content": system_prompt}, {"role": "user", "content": context}],
            stream=False,
        )
    except Exception as exc:  # noqa: BLE001 - 外部模型异常统一转为可定位错误
        raise ResearchDeliverableError("deliverable_model_failed", "交付物生成模型调用失败") from exc
    return _parse_json_object(str(response.content or ""), stage=stage)


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
        "file_id": str(paper.file_id),
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
        return [str(item).strip() for item in values if str(item).strip()]
    if isinstance(values, str) and values.strip():
        return [values.strip()]
    return []


async def _latest_structure(kb_id: str, paper_id: str, current_user: User) -> tuple[dict[str, Any], str]:
    from yuxi.services.academic_paper_analysis_service import get_latest_paper_analysis

    analysis = await get_latest_paper_analysis(kb_id=kb_id, paper_id=paper_id, current_user=current_user)
    if not analysis:
        return {}, ""
    return _structure_of(_stage_payload(analysis)), str(analysis.get("run_id") or "")


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
        structure, analysis_run_id = await _latest_structure(kb_id, str(paper.paper_id), current_user)
        cards.append(
            {
                **_paper_meta(paper),
                "analysis_run_id": analysis_run_id,
                "analysis": {
                    "problem": str(structure.get("problem") or "").strip(),
                    "method": str(structure.get("method") or "").strip(),
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
    lines = [
        f"# 论文分析报告：{paper_title or run.get('paper_id', '')}",
        "",
        f"- 运行 ID：{run.get('run_id', '')}",
        f"- 状态：{run.get('status', '')}",
        f"- 策略：{run.get('strategy', '')}",
        f"- 生成时间：{_now_text()}",
        "",
    ]
    structure = _structure_of(stages)
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


DIAGRAM_KINDS = {"method": "flow", "topics": "hub"}


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
    else:
        keywords = _string_list(paper.keywords)
        if not keywords:
            raise ResearchDeliverableError("diagram_no_data", "该论文没有可用的关键词，无法生成主题图")
        svg = _render_hub_svg(f"主题图：{title}", title, keywords[:8])
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
