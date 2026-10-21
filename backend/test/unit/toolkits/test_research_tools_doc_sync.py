"""研究工具文档同步守卫。

`docs/agents/tools-system.md` 的科研工具表是面向开发者的能力清单，新增工具后必须同步，
否则文档会逐步失真。这里比对文档中出现的 `research_*` 名称与 `RESEARCH_TOOLS` 注册表。
"""

import re
from pathlib import Path

from yuxi.agents.toolkits.research import RESEARCH_TOOLS

TOOLS_DOC = Path(__file__).resolve().parents[4] / "docs" / "agents" / "tools-system.md"


def test_tools_doc_lists_every_registered_tool():
    documented = set(re.findall(r"research_[a-z_]+", TOOLS_DOC.read_text(encoding="utf-8")))
    registered = {tool.name for tool in RESEARCH_TOOLS}

    assert sorted(registered - documented) == []
    assert sorted(documented - registered) == []
