"""研究助手上下文提示词测试。"""

from types import SimpleNamespace

from yuxi.agents.buildin.chatbot.prompt import build_prompt_with_context


def _context(**research_context):
    return SimpleNamespace(research_context=research_context, system_prompt="")


def test_research_context_prompt_includes_surface_and_selected_paper():
    prompt = build_prompt_with_context(
        _context(
            kb_id="kb-1",
            kb_name="Demo KB",
            project_id="project-1",
            project_title="Graph RAG",
            surface="library",
            selection={"type": "paper", "id": "paper-1", "title": "Attention is All you Need"},
        )
    )

    assert "知识库: Demo KB" in prompt
    assert "项目: Graph RAG" in prompt
    assert "当前工作区: 论文库" in prompt
    assert "当前选中: 论文 Attention is All you Need（ID: paper-1）" in prompt
    assert "这篇论文 / 当前检索 / 当前综述" in prompt


def test_research_context_prompt_labels_search_and_synthesis_selections():
    search_prompt = build_prompt_with_context(
        _context(
            kb_id="kb-1",
            surface="search",
            selection={"type": "search_run", "id": "run-1", "title": "graph rag"},
        )
    )
    synthesis_prompt = build_prompt_with_context(
        _context(
            kb_id="kb-1",
            surface="synthesis",
            selection={"type": "synthesis_run", "id": "syn-1", "title": "evidence review"},
        )
    )

    assert "当前工作区: 智能检索" in search_prompt
    assert "当前选中: 检索运行 graph rag（ID: run-1）" in search_prompt
    assert "当前工作区: 证据综述" in synthesis_prompt
    assert "当前选中: 证据综述 evidence review（ID: syn-1）" in synthesis_prompt


def test_research_context_prompt_without_selection_stays_minimal():
    prompt = build_prompt_with_context(_context(kb_id="kb-1", surface="library"))

    assert "当前工作区: 论文库" in prompt
    assert "当前选中:" not in prompt
