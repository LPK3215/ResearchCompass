"""论文分析 Markdown 导出的对象格式化测试。"""

from yuxi.services.research_deliverable_service import _render_analysis_markdown


def test_render_analysis_markdown_formats_direction_objects_and_title_fallback():
    run = {
        "run_id": "run-1",
        "paper_id": "paper-1",
        "status": "success",
        "strategy": "multi_agent",
        "result": {
            "structure": {"title": "Attention is All you Need"},
            "research_gaps": {
                "gaps": [],
                "future_directions": [
                    {"direction": "研究稀疏注意力", "basis": "Section 4", "confidence": "high"},
                    "扩展到多模态",
                ],
            },
        },
    }

    markdown = _render_analysis_markdown(run, "")

    assert "# 论文分析报告：Attention is All you Need" in markdown
    assert "研究稀疏注意力（依据：Section 4）（置信度：high）" in markdown
    assert "扩展到多模态" in markdown
    assert "[object Object]" not in markdown
    assert "{'direction'" not in markdown
