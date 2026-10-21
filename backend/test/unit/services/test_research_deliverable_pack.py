"""ResearchCompass 汇报包渲染测试：对比表 + 方法流程图。"""

from yuxi.services.research_deliverable_service import render_pack_html, render_pack_markdown


def _pack() -> dict:
    return {
        "title": "测试汇报",
        "audience": "组会",
        "generated_at": "2026-10-21 12:00",
        "outline": [{"section": "背景", "bullets": ["研究问题"]}],
        "comparison": {"columns": ["论文", "方法"], "rows": [["Paper A", "RAG"]]},
        "talking_points": ["要点"],
        "risks": ["局限"],
        "papers": [{"paper_id": "p1", "title": "Paper A"}],
        "diagrams": [
            {
                "paper_id": "p1",
                "title": "Paper A",
                "steps": ["检索证据", "生成综述"],
                "svg": '<svg data-test="flow"></svg>',
            }
        ],
    }


def test_pack_html_embeds_method_diagram():
    html = render_pack_html(_pack())
    assert "方法流程图" in html
    assert '<svg data-test="flow"></svg>' in html
    assert "Paper A" in html


def test_pack_markdown_lists_method_steps():
    markdown = render_pack_markdown(_pack())
    assert "## 方法流程图" in markdown
    assert "1. 检索证据" in markdown
    assert "2. 生成综述" in markdown
    assert "SVG 流程图" in markdown
