"""论文分析 Markdown 导出的对象格式化测试。"""

from types import SimpleNamespace

import pytest
from yuxi.services import research_deliverable_service
from yuxi.services.research_deliverable_service import _render_analysis_markdown, _topic_items


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


class FlakyModel:
    def __init__(self):
        self.calls = 0

    async def call(self, messages, stream=False):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("transient model failure")
        return SimpleNamespace(content='{"outline": []}')


@pytest.mark.asyncio
async def test_call_model_retries_once(monkeypatch):
    model = FlakyModel()
    monkeypatch.setattr(research_deliverable_service, "select_model", lambda **kwargs: model)

    result = await research_deliverable_service._call_model("test:model", "system", "context", stage="test")

    assert result == {"outline": []}
    assert model.calls == 2


def test_topic_items_skips_none_and_formats_direction_objects():
    result = {
        "research_gaps": {
            "gaps": [],
            "future_directions": [
                {"direction": "研究稀疏注意力", "basis": "Section 4", "confidence": "high"},
            ],
        },
        "unsupported_claims": [{"statement": "补充可复现性证据"}, {"claim": None}],
    }

    items = _topic_items(result)

    assert [item["topic"] for item in items] == ["研究稀疏注意力", "补充可复现性证据"]
    assert items[0]["basis"] == "Section 4"
    assert items[0]["confidence"] == "high"
    assert all(item["topic"].lower() not in {"none", "null"} for item in items)
