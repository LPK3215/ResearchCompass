from dataclasses import dataclass, field

from yuxi.agents.context import BaseContext


@dataclass(kw_only=True)
class ChatBotContext(BaseContext):
    runtime_agent_slug: str = field(
        default="",
        metadata={"configurable": False, "hide": True},
    )

    research_tools_enabled: bool = field(
        default=False,
        metadata={"configurable": False, "hide": True},
    )

    research_context: dict = field(
        default_factory=dict,
        metadata={
            "name": "研究上下文",
            "configurable": False,
            "hide": True,
            "description": "ResearchCompass 工作区注入的知识库、项目和当前界面上下文。",
        },
    )

    subagents: list[str] | None = field(
        default=None,
        metadata={
            "name": "子智能体",
            "options": [],
            "description": "可选子智能体列表，为空表示启用当前用户可见的全部子智能体。",
            "type": "list",
            "kind": "subagents",
        },
    )
