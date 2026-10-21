"""校验研究工具在定义、注册与 research-copilot 启用清单三处保持一致。

用法：在 backend 目录执行 ``uv run --no-sync python scripts/check_research_tool_parity.py``。
发现不一致时返回退出码 1，可直接用于发布前检查。
"""

from __future__ import annotations

import sys

from yuxi.agents.toolkits.research import RESEARCH_TOOLS
from yuxi.repositories.agent_repository import RESEARCH_COPILOT_TOOL_SLUGS


def main() -> int:
    defined = {tool.name for tool in RESEARCH_TOOLS}
    enabled = {name for name in RESEARCH_COPILOT_TOOL_SLUGS if name.startswith("research_")}
    missing_in_enabled = sorted(defined - enabled)
    missing_in_tools = sorted(enabled - defined)
    print(f"defined={len(defined)} enabled={len(enabled)}")
    if missing_in_enabled:
        print("未加入 research-copilot 启用清单: " + ", ".join(missing_in_enabled))
    if missing_in_tools:
        print("启用清单中存在但未定义的工具: " + ", ".join(missing_in_tools))
    if missing_in_enabled or missing_in_tools:
        return 1
    print("parity ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
