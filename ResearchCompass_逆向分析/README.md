# ResearchCompass 逆向分析报告 · 索引

> 逆向对象：本地仓库 `ResearchCompass`（工作区 `d:/Develop/SourceCode/agent-projects/ResearchCompass`）
> 分析方式：**方式 A（直接读取本地源码）**，所有结论均指向具体文件路径与函数/行号
> 分析日期：2026-08-03
> 分析方法论：自顶向下读源码，重点还原「二次开发层（ResearchCompass）」叠加在「通用框架层（Yuxi）」之上的设计意图、技术取舍与接缝风险

## 仓库一句话定性

ResearchCompass（科研罗盘）= 在通用 RAG / 知识图谱 / 多 Agent 框架 **Yuxi** 之上，二次开发的**科研领域智能体平台**。
Yuxi 提供地基（LangGraph 运行态、FastAPI、PostgreSQL、Redis/ARQ、LightRAG 知识图谱、向量库、沙盒、鉴权、中间件），
ResearchCompass 只往三个持久化扩展槽「填空」：①科研业务 service、②科研领域工具、③学术分块解析器 + 引用图同步器。
**它不重写框架，而是用框架原语拼装科研业务流程。**

## 文档结构

| 文件 | 内容 | 核心结论 |
|------|------|----------|
| [0-仓库画像.md](./0-仓库画像.md) | 类型判断 + 依据 | 多语言 monorepo / 平台 / 生产级；L2 框架 + L3 领域层叠层 |
| [1-架构全景.md](./1-架构全景.md) | 模块划分、依赖、核心抽象、调用链 | 三层架构：框架地基 / 科研业务 service / HTTP+Agent 暴露层；接缝在 `run_worker.py` 与 `research_router.py` |
| [2-核心数据流.md](./2-核心数据流.md) | 6 条关键执行链路 | 检索→证据综述→论文分析→引用图→Copilot→项目治理，逐段标注文件与函数 |
| [3-关键模块拆解.md](./3-关键模块拆解.md) | 5 个核心模块深拆 | 学术分块 / 混合检索 / 证据综述 / 四阶段 LangGraph / 引用图谱同步 |
| [4-设计取舍与坑.md](./4-设计取舍与坑.md) | 亮点 + 局限 + 隐患 | 聪明的"填空式"二次开发；隐患集中在框架↔领域接缝缺乏集成测试 |
| [5-可复用资产.md](./5-可复用资产.md) | 可迁移的模式/代码 | 学术章节分块、领域错误类型→HTTP 状态码映射、Run 状态机、引用图冲突处理 |
| [6-定向产出.md](./6-定向产出.md) | 按"竞赛方案参考 + 通用学习" | 倒推参赛方案的借鉴点；若做类似项目第一步抄哪、改哪 |
| [核实清单.md](./核实清单.md) | 已读源码 / 待核实 | 绝大多数结论已读源码确认；少数运行态细节标注 ⚠️ 待核实 |

## 关键证据文件（逆向锚点）

- 框架边界声明：`AGENTS.md`、`ARCHITECTURE.md`
- 科研业务 HTTP 契约：`backend/server/routers/research_router.py`（1747 行，全部科研接口入口）
- 领域 service 集合：`backend/package/yuxi/services/research_*.py`、`academic_*.py`
- Agent 运行时接缝：`backend/package/yuxi/services/run_worker.py`（`RESEARCH_COPILOT_AGENT_SLUG`、`RESEARCH_CONTEXT_SOURCES`）
- 领域工具注册：`backend/package/yuxi/agents/toolkits/research/tools.py`（`@tool` 注册）
- 学术分块解析器：`backend/package/yuxi/knowledge/chunking/ragflow_like/parsers/academic.py`
- 四阶段工作流：`backend/package/yuxi/services/academic_paper_analysis_workflow.py`（LangGraph `StateGraph`）
