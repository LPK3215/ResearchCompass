# 工具系统

> 本文描述的能力由开源智能体框架 Yuxi 提供，ResearchCompass 复用该能力作为科研智能体平台的通用底座。

Yuxi 的工具系统基于注册机制，支持多种工具类型的动态组装。

## 工具注册机制

Yuxi 的工具系统采用 `@tool` 装饰器注册机制，核心位于 `backend/package/yuxi/agents/toolkits/registry.py`。

### @tool 装饰器

```python
from yuxi.agents.toolkits.registry import tool

@tool(category="buildin", tags=["示例"], display_name="示例工具")
def example_tool(text: str) -> str:
    """示例工具：返回处理后的文本"""
    ...
```

装饰器参数：
- **category**: 工具分类，用于分组，例如 `buildin`、`knowledge`、`debug`
- **tags**: 标签列表，用于前端展示
- **display_name**: 显示名称（给人看的名字）
- **icon**: 图标名称（可选）

### 自动发现

导入 `toolkits` 包时会自动触发注册：

```python
from yuxi.agents.toolkits import buildin, debug  # 触发模块内 @tool 装饰器执行
```

`toolkits/__init__.py` 会导入 `buildin` 与 `debug` 模块；知识库工具由内置 `knowledge-base` Skill 的依赖显式注册，而不是作为所有 Agent 的默认工具。

## 工具分类

### 内置工具 (buildin)

| 工具 | 说明 |
|------|------|
| `ask_user_question` | 向用户发起交互式提问 |
| `ocr_parse_file` | 将 uploads、outputs 或 workspace 中的 PDF、Office 或图片文件转换为 Markdown |
| `present_artifacts` | 展示 Agent 沙盒 outputs 目录下的产物文件 |
| `install_skill` | 从沙盒路径或 Git 来源安装当前用户私有 Skill，并激活当前主智能体会话；子智能体禁用 |
| `tavily_search` | Tavily 网页搜索（需配置 `TAVILY_API_KEY`） |

Qwen-Image 生成能力已迁移为内置 Skill `image-gen`。模型调用与图片下载在 Agent 沙盒中完成，生成后的图片保存到 `/home/gem/user-data/outputs/`，再通过 `present_artifacts` 展示。

### 知识库工具 (kbs)

知识库工具使用 `@tool(category="knowledge")` 注册，并通过内置 `knowledge-base` Skill 的 `tool_dependencies` 按需加载。`get_common_kb_tools()` 仍可用于直接获取完整工具列表：

```python
from yuxi.agents.toolkits.kbs import get_common_kb_tools

kb_tools = get_common_kb_tools()
# 返回: [list_kbs, get_mindmap, query_kb, find_kb_document, open_kb_document, search_file, download_kb_file]
```

| 工具 | 说明 |
|------|------|
| `list_kbs` | 列出用户可访问的知识库 |
| `get_mindmap` | 获取知识库的思维导图结构 |
| `query_kb` | 按 `kb_id` 检索内容，返回结构化的 `kb_id`、`file_id` 与命中片段 |
| `find_kb_document` | 在已知文件内按关键词或正则定位内容 |
| `open_kb_document` | 按 `file_id` 分段打开知识库文档（默认窗口 1800 行） |
| `search_file` | 按文件名在指定或全部可见知识库中搜索文件 |
| `download_kb_file` | 下载知识库内文件到沙盒 outputs 目录，便于后续工具读取 |

### 科研工具 (research)

科研工具使用 `@tool(category="research")` 注册，是 ResearchCompass 在 Yuxi 框架之上扩展的核心能力，由 `research-copilot` 主智能体调用，覆盖项目治理、论文检索、证据综述、分析、图谱、评测与交付物导出等科研闭环场景。每个能力都同时具备工作台界面入口与 AI 工具入口（双通道），共 65 个工具。具体工具如下：

| 工具 | 说明 |
|------|------|
| `research_get_context` | 获取当前线程的科研上下文（当前项目、用户偏好、可用知识库等） |
| `research_list_projects` | 列出指定知识库下的科研项目 |
| `research_get_project` | 获取指定项目的详细信息 |
| `research_create_project` | 在指定知识库下创建科研项目 |
| `research_update_project` | 更新项目名称、描述、状态等字段 |
| `research_set_project_status` | 切换项目状态（如 active、archived） |
| `research_delete_project` | 删除指定项目 |
| `research_get_project_plan` | 获取项目计划，包括里程碑、任务、关联资产 |
| `research_create_milestone` | 创建项目里程碑 |
| `research_update_milestone` | 更新里程碑字段 |
| `research_delete_milestone` | 删除里程碑 |
| `research_create_task` | 在里程碑下创建任务 |
| `research_update_task` | 更新任务字段 |
| `research_delete_task` | 删除任务 |
| `research_search_papers` | 在指定知识库中检索论文，支持混合检索与严格图谱检索模式 |
| `research_get_search_run` | 查询检索任务的运行结果与命中论文列表 |
| `research_create_synthesis` | 基于检索到的论文生成证据综述 |
| `research_get_synthesis` | 查询综述结果，包括论点、主题、局限和研究空白 |
| `research_regenerate_synthesis` | 重新生成已有综述 |
| `research_export_synthesis` | 将综述结果导出为 MD 文件并展示给用户下载 |
| `research_list_project_asset_candidates` | 列出可作为项目资产候选的论文或综述 |
| `research_add_project_assets` | 将候选资产添加到项目 |
| `research_list_project_assets` | 列出项目已有的资产 |
| `research_remove_project_asset` | 移除项目资产 |
| `research_link_asset_to_plan` | 将资产关联到里程碑或任务 |
| `research_unlink_asset_from_plan` | 解除资产与计划项的关联 |
| `research_run_paper_analysis` | 对单篇论文发起结构化分析（单 Agent / 四阶段多 Agent） |
| `research_get_paper_analysis` | 读取指定分析运行或某篇论文最近一次分析结果 |
| `research_export_project_report` | 将项目导出为 Markdown / Word / CSV 报告 |
| `research_export_papers` | 将论文元数据导出为 BibTeX / Markdown / RIS / CSV |
| `research_list_evidence` | 查询当前知识库已录入证据与验证状态 |
| `research_create_evidence` | 将知识片段录入为待验证证据 |
| `research_verify_evidence` | 验证、作废或归档证据（综述失败时的补救入口） |
| `research_get_evidence_audit` | 读取证据的状态活动、综述引用与项目影响 |
| `research_sync_academic_graph` | 把知识库论文同步到引用图谱，返回同步运行 ID |
| `research_get_academic_graph` | 读取引用图谱网络，可按中心论文展开 |
| `research_get_graph_relations` | 读取某篇图谱论文的可解释两跳引用关系 |
| `research_list_graph_conflicts` | 读取一次同步运行中的元数据冲突 |
| `research_get_trends` | 统计发表趋势、关键词演进与新兴方向 |
| `research_get_opportunities` | 给出研究机会排序与判断依据 |
| `research_list_project_risks` | 查询项目风险登记项与处置状态 |
| `research_create_project_risk` | 在当前项目登记风险 |
| `research_transition_project_risk` | 推进风险处置状态（需审批） |
| `research_list_project_risk_activities` | 读取一条风险的处置活动记录 |
| `research_add_task_dependency` | 声明任务依赖，循环依赖会被拒绝 |
| `research_remove_task_dependency` | 移除任务依赖（需审批） |
| `research_list_user_studies` | 查询用户评测、参与人数与回收情况 |
| `research_create_user_study` | 创建匿名单次邀请的用户评测（需审批） |
| `research_get_user_study_report` | 读取 SUS、任务评分与匿名原始响应 |
| `research_close_user_study` | 关闭用户评测（需审批） |
| `research_export_user_study` | 导出用户评测 CSV |
| `research_create_analysis_evaluation` | 创建单/多 Agent 分析对比，供人工 A/B 盲审 |
| `research_list_analysis_evaluations` | 查询分析对比实验与状态 |
| `research_get_analysis_evaluation` | 读取分析对比汇总报告（仅统计真实人工评分） |
| `research_search_external_papers` | 在 Semantic Scholar 搜索公开论文 |
| `research_import_external_paper` | 按标题或 DOI 导入公开论文（需审批） |
| `research_list_eval_datasets` | 查询 RAG 评估基准（仅管理员） |
| `research_run_eval` | 基于评估基准发起 RAG 评估（仅管理员） |
| `research_get_eval_run` | 读取评估逐题结果与指标（仅管理员） |
| `research_list_eval_experiments` | 查询消融实验（仅管理员） |
| `research_get_eval_experiment` | 读取消融实验报告与指纹（仅管理员） |
| `research_export_paper_cards` | 导出文献卡片（元数据 + 分析摘要，MD / JSON） |
| `research_export_paper_analysis` | 把论文分析运行导出为 Markdown 报告 |
| `research_generate_paper_brief` | 生成单篇论文分层摘要（一句话 / 一段 / 一页） |
| `research_generate_presentation_pack` | 基于 1 到 10 篇论文生成汇报包（MD / HTML） |

## 工具组装

工具组装在 Graph 创建阶段完成。内置 Agent 会先调用 `prepare_agent_runtime_context` 过滤当前用户可用资源，再调用 `resolve_configured_runtime_tools(context)` 加载已配置工具：

1. **基础工具**：从 `context.tools` 中按名称筛选
2. **MCP 工具**：根据 `context.mcps` 加载 MCP 服务器工具
3. **Skill 依赖工具**：由 `SkillsMiddleware` 在 Skill 激活后按需追加，包括 `knowledge-base` 绑定的知识库工具

```python
from yuxi.agents.context import prepare_agent_runtime_context
from yuxi.agents.toolkits.service import resolve_configured_runtime_tools

context = await prepare_agent_runtime_context(context, user=current_user, db=db)
tools = await resolve_configured_runtime_tools(context)
```

## Skills 集成

Skills 与工具是两种不同的扩展机制。工具是具体的功能实现，而 Skills 是包含提示词、工具依赖和元数据的完整技能包。通过 `context.skills` 配置 Skills 时，对应的技能文件会被挂载到沙盒的 `/home/gem/skills/<slug>/...`，智能体可以通过读取 SKILL.md 来了解如何使用这些技能。

关于 Skills 的详细机制，请参阅 [Skills 管理](./skills-management.md)。
