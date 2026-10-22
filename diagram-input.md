# ResearchCompass 出图输入蓝图

## 1. 组件清单

FE-WORKBENCH｜科研罗盘工作台｜承载研究项目、论文库、智能检索、证据综述、引用图谱、研究趋势/机会等 9 个 tab 与工具栏的科研前端主入口（类型：前端）
FE-COPILOT｜AI 研究助手｜以当前知识库、研究项目与页面选中对象为 scope 发起对话，并调用 `research_*` 工具（类型：前端）
BE-RESEARCH-API｜科研 API 路由（`research_router.py`）｜提供 `/api/research` 下的检索、综述、分析、项目、图谱与导出接口，只做鉴权、请求解析与响应装配（类型：后端）
BE-PAPER-KB｜论文库与知识库域｜维护论文元数据、章节 chunk 与 Milvus 向量索引，提供 `Academic Paper` 分块入库、论文/证据定位与混合召回（类型：后端）
BE-SEARCH｜科研检索服务（`research_search_service.py`）｜编排查询改写、混合召回、RRF、重排序、论文证据聚合，以及严格图谱模式的 PPR 扩展与融合排序（类型：后端）
BE-SYNTHESIS｜证据综述服务（`research_synthesis_service.py`）｜以本地混合检索快照为唯一证据源生成结构化综述，并校验 `evidence_chunk_ids` 与引用覆盖率（类型：后端）
BE-PAPER-ANALYSIS｜论文分析服务（`academic_paper_analysis_service.py`）｜执行 `single_agent` 全文分析或 `multi_agent` 四阶段分析：`structure` → `innovations` → `methodology` → `gaps`（类型：后端）
BE-GRAPH｜引用图谱服务（`academic_graph_sync_service.py` / `AcademicGraphService`）｜对齐 Semantic Scholar 元数据、写入引用边，并提供图谱状态、邻域与 PPR 扩展（类型：后端）
BE-PROJECT｜研究项目服务（`research_project_service.py` / `research_project_report_service.py`）｜维护项目、里程碑、任务、风险与成果归集，生成确定性进度/健康度与 Markdown/Word/CSV 报告（类型：后端）
BE-RUNTIME｜智能体与异步任务运行时｜承载 `/api/chat/call` 的 agent run、`research-copilot` 工具调用，以及 `research_synthesis` / `academic_paper_analysis` / `academic_graph_sync` 异步任务（Tasker / ARQ worker）（类型：后端）
INFRA-STORE｜存储层｜PostgreSQL 存业务/运行记录，Redis 承载运行事件与 ARQ 队列，MinIO 存文件，Milvus 存向量，Neo4j 存引用图谱（类型：存储）
EXT-SERVICES｜外部服务｜Semantic Scholar / OpenAlex / Crossref 提供文献元数据，LLM 与 embedding/rerank 提供模型推理，MinerU/PaddleX 提供 PDF 解析（类型：外部）

## 2. 关系清单

（architecture 图可优先画 FE/BE 调用与跨层数据流；同一 `INFRA-STORE` 的多条落库关系可合并为一条，dataflow 图再展开。）

FE-WORKBENCH → BE-RESEARCH-API：`/api/research` 请求（检索、综述、分析、项目、图谱、导出），实线主路径
FE-WORKBENCH → BE-PAPER-KB：上传 PDF、选择 `Academic Paper` 分块（知识库接口），实线主路径
FE-WORKBENCH → FE-COPILOT：传入当前知识库、研究项目与页面选中对象（`ResearchCopilotScope`），实线主路径
FE-COPILOT → BE-RESEARCH-API：`POST /api/research/copilot/thread` 创建/复用 `research-copilot` 作用域会话，实线主路径
FE-COPILOT → BE-RUNTIME：`/api/chat/call` 发起 agent run（消息 + scope），实线主路径
BE-RUNTIME → FE-COPILOT / FE-WORKBENCH：SSE/轮询回传运行事件、工具调用与 `human_approval_required` 审批请求，虚线反馈
BE-RUNTIME → BE-SEARCH / BE-SYNTHESIS / BE-PAPER-ANALYSIS / BE-GRAPH / BE-PROJECT / BE-PAPER-KB：`research_*` 工具调用；与人工 UI 共用同一 service 与权限校验（双通道），实线主路径
BE-RESEARCH-API → BE-PAPER-KB：论文列表、详情、chunk 与证据定位查询，实线主路径
BE-RESEARCH-API → BE-SEARCH：`search_papers`（query、top_k、recall_top_k、year_from/year_to、retrieval_mode），实线主路径
BE-SEARCH → BE-PAPER-KB：`knowledge_base.aquery(... search_mode="hybrid", use_reranker=True)`，实线主路径
BE-SEARCH → BE-GRAPH：`strict_hybrid_citation_graph` 预检 + `expand_papers_by_ppr(depth=2, damping=0.85)`，实线主路径（条件分支）
BE-SEARCH → EXT-SERVICES：查询改写与 rerank 的模型调用，实线主路径
BE-SEARCH → INFRA-STORE：`ResearchSearchRun` 保存论文排序、证据、图谱扩展与阶段耗时，实线主路径
BE-RESEARCH-API → BE-SYNTHESIS：`enqueue_research_synthesis`（创建 run 后入队），实线主路径
BE-SYNTHESIS → BE-SEARCH：`local_hybrid` 检索生成证据快照（至少 2 篇含证据论文），实线主路径
BE-SYNTHESIS → EXT-SERVICES：按 `SYNTHESIS_SYSTEM_PROMPT` 生成结构化 JSON，实线主路径
BE-SYNTHESIS → INFRA-STORE：`ResearchSynthesisRun`、`ResearchEvidenceCitation` 与检索快照，实线主路径
BE-RESEARCH-API → BE-PAPER-ANALYSIS：`enqueue_paper_analysis`（`paper_id`、`strategy`、`model`），实线主路径
BE-PAPER-ANALYSIS → BE-PAPER-KB：读取单篇论文全文 chunk 作为分析上下文，实线主路径
BE-PAPER-ANALYSIS → BE-GRAPH：`gaps` 阶段使用引用图谱邻域（出/入引 + 两跳），实线主路径（条件分支）
BE-PAPER-ANALYSIS → EXT-SERVICES：`single_agent` 或四阶段 LLM 调用，实线主路径
BE-PAPER-ANALYSIS → INFRA-STORE：`AcademicPaperAnalysisRun` 阶段结果，实线主路径
BE-RESEARCH-API → BE-GRAPH：`enqueue_academic_graph_sync`、图谱网络与关系查询，实线主路径
BE-GRAPH → EXT-SERVICES：Semantic Scholar 拉取引用/参考文献（`SEMANTIC_SCHOLAR_API_KEY`），实线主路径
BE-GRAPH → INFRA-STORE：`AcademicGraphPaper` / `AcademicCitation` / `AcademicGraphSyncRun`，实线主路径
BE-RESEARCH-API → BE-PROJECT：项目、里程碑、任务、风险、成果归集与报告导出接口，实线主路径
BE-PROJECT → INFRA-STORE：`ResearchProject` / `ResearchProjectMilestone` / `ResearchProjectTask` / `ResearchProjectAsset` / `ResearchProjectActivity`，实线主路径
BE-PROJECT → BE-SEARCH / BE-SYNTHESIS / BE-PAPER-ANALYSIS / BE-PAPER-KB：归集成果引用与快照（不复制原成果），虚线反馈
BE-SYNTHESIS / BE-PAPER-ANALYSIS / BE-GRAPH → BE-RUNTIME：提交 `research_synthesis` / `academic_paper_analysis` / `academic_graph_sync` 任务，实线主路径
BE-RUNTIME → INFRA-STORE：agent run、task 状态与 Redis 事件，实线主路径
BE-PAPER-KB → INFRA-STORE：`KnowledgeFile` / `KnowledgeChunk` / `AcademicPaper` 与 Milvus 向量，实线主路径
BE-PAPER-KB → EXT-SERVICES：MinerU/PaddleX 解析 PDF、embedding 模型，实线主路径

## 3. 分层分组

- `boundary: 前端层`：FE-WORKBENCH、FE-COPILOT。
- `boundary: 接口层`：BE-RESEARCH-API。
- `boundary: 科研领域服务层`：BE-PAPER-KB、BE-SEARCH、BE-SYNTHESIS、BE-PAPER-ANALYSIS、BE-GRAPH、BE-PROJECT。
- `boundary: 运行与存储底座`：BE-RUNTIME、INFRA-STORE。
- `boundary: 外部层`：EXT-SERVICES。
- `双通道`：人工 UI 通道为 `FE-WORKBENCH → BE-RESEARCH-API → 领域服务`；AI 工具通道为 `FE-COPILOT → BE-RUNTIME → research_* 工具 → 领域服务`；两条通道共用同一 service 与权限校验，不另建第二套业务逻辑。
- 运行位置：`web-dev` 承载前端；`api-dev` 承载 BE-RESEARCH-API 与 Tasker；`worker-dev` 承载 ARQ agent run；PostgreSQL、Redis、MinIO、Milvus、Neo4j 为 Compose 存储服务。图中只画逻辑边界，不画容器编排细节。

## 4. 主流程

① 准备知识库与模型：新建 Milvus 知识库 → 上传 PDF → 选择 `Academic Paper` 分块 → 解析、索引，状态变为已入库（FE-WORKBENCH → BE-PAPER-KB → INFRA-STORE / EXT-SERVICES）。
② 锁定选题与文献集：查看研究趋势、研究机会、引用图谱，必要时导入公开论文 → 收敛出 20～50 篇核心论文（FE-WORKBENCH → BE-GRAPH / EXT-SERVICES；趋势/机会为支路入口）。
③ 把文献读薄：对单篇论文跑 `single_agent` 全文分析，或 `multi_agent` 四阶段分析（`structure` → `innovations` → `methodology` → `gaps`）→ 得到每篇的结构化摘要（BE-PAPER-ANALYSIS）。
④ 建立证据账本【核心】：选择 `local_hybrid` 或 `strict_hybrid_citation_graph` → `查询改写` → 向量 + BM25 召回 → `RRF` → `重排序` → `论文证据聚合` → 得到带 `bm25` / `vector` / `hybrid` / `rerank` 四路分数的可复查证据片段；严格图谱模式再叠加 `graph` / `fusion` 与 PPR 扩展（BE-SEARCH）。
⑤ 写文献综述：`retrieving` → `synthesizing` → `validating` → `completed`，产出 `executive_summary`、`themes`、`claims`、`contradictions`、`limitations`、`research_gaps` 与 `unsupported_claims`（界面文案：未支持结论），每条结论绑定 `evidence_chunk_ids`（BE-SYNTHESIS）。
⑥ 推进课题：研究项目维护里程碑、任务、风险与成果归集 → 进度由任务自动计算，健康度按固定规则给出 `已逾期` / `有风险` / `进展正常` / `尚未规划` → 导出 Markdown/Word/CSV 报告（BE-PROJECT）。
⑦ 变成论文文字：综述导出 Markdown/DOCX、论文库导出 BibTeX/RIS、AI 研究助手基于证据回答与整理写作素材（不代写论文）（BE-SYNTHESIS / FE-COPILOT）。
⑧ 自证严谨【支路，不进入主图节点】：RAG 评估、消融实验、分析对比、用户评测（SUS），只在需要“评测图”时单独展开。

## 5. 关键术语

- 检索模式：`local_hybrid`、`strict_hybrid_citation_graph`（界面名：`本地混合`、`严格图谱`）。
- 检索阶段：`查询改写`、`RRF`、`重排序`、`论文证据聚合`。
- 检索分数：`四路分数`（`bm25`、`vector`、`hybrid`、`rerank`）；严格图谱模式另有 `graph`、`fusion`。
- 图谱：`PPR`、`academic_citation_ppr_undirected`、`graph_depth`（值 `2`）、`ppr_damping`（值 `0.85`）、`两跳关系`。
- 分块：`Academic Paper`。
- 综述：`evidence-synthesis-v1`、`retrieving`、`synthesizing`、`validating`、`completed`、`executive_summary`、`themes`、`claims`、`contradictions`、`limitations`、`research_gaps`、`unsupported_claims`、`evidence_chunk_ids`、`chunk_id`、`paper_id`。
- 综述界面文案：`主题综述`、`核心结论`、`证据矛盾`、`研究局限`、`研究空白`、`未支持结论`。
- 分析：`single_agent`、`multi_agent`、`structure`、`innovations`、`methodology`、`gaps`。
- 项目资产：`paper`、`search_run`、`synthesis_run`、`analysis_run`、`evaluation_experiment`、`evidence`。
- 项目健康度：`已逾期`、`有风险`、`进展正常`、`尚未规划`。
- 运行与外部：`research-copilot`、`research_synthesis`、`academic_paper_analysis`、`academic_graph_sync`、`human_approval_required`、`/api/research`、`SEMANTIC_SCHOLAR_API_KEY`。
- 评测支路术语（仅出评测图时使用）：`Recall@K`、`F1`、`SUS`。
- 概念：`双通道`。

## 6. 明确排除项

- 不画测试与验证产物：`backend/test/`、`test-results/`、`*.log`、pytest 夹具、冒烟/浏览器验收记录；原因是它们验证系统，不是系统结构本身。
- 不画配置与部署细节：`.env`、`docker-compose.yml`、`docker-compose.prod.yml`、Dockerfile、`scripts/`、`Makefile`；原因是运行环境与编排，不是本图对象；只在分层分组中用 `web-dev` / `api-dev` / `worker-dev` 说明运行位置。
- 不画过程性文档与过时材料：`docs/project/临时错误清单-2026-07-28.md`、`docs/project/历史目标.md`、`docs/project/项目统计-自动生成.md`、`docs/project/验证记录-2026-10-20.md`、`docs/project/验证记录-2026-10-22.md`、`docs/vibe/` 下的历史方案；原因是过程归档或自动生成，不作为当前结构依据。
- 不画学术引用、公式推导与实验参数：文档引用的外部论文、公式、token 预算（如 `DEFAULT_SYNTHESIS_INPUT_BUDGET`）、实验数值与限流参数；原因是属于论证或可调参数，不是组件/流程结构。
- 次要分支不单独成节点：RAG 评估、消融实验、分析对比、用户评测（SUS）、研究趋势、研究机会、论文对话、对照翻译、阅读笔记、文献筛选、引文滚雪球、重复论文检测/合并、文献卡片/汇报包/结构图导出、公开论文导入、BibTeX/RIS 导入；原因是它们不改变“建库 → 检索 → 综述 → 项目 → 导出”的主链路，且会把主节点推过 12 个。
- 不画框架通用能力：Lite 模式、认证与多租户、沙盒、深度研究、Skills/MCP/SubAgents；原因是 Yuxi 通用底座能力，且研究助手明确“不执行系统命令、不直接写任意文件”，与科研主链路不是同一层问题。
- 合并说明（为控制在 12 个主节点内）：`research_paper_service.py` 与知识库运行时合并为 `BE-PAPER-KB`，因为共享 `KnowledgeFile` / `KnowledgeChunk` / `AcademicPaper` / Milvus 数据边界；`/api/chat/call`、`research-copilot` 工具注册表、Tasker、ARQ worker 合并为 `BE-RUNTIME`，因为同属运行与异步执行边界；PostgreSQL、Redis、MinIO、Milvus、Neo4j 合并为 `INFRA-STORE`，各自角色已在组件职责中列明；Semantic Scholar、OpenAlex、Crossref、LLM/embedding/rerank、MinerU/PaddleX 合并为 `EXT-SERVICES`，图内不展开各自调用细节；`ResearchSearchWorkspace`、`ResearchProjectWorkspace`、`PaperDetailDrawer`、`SynthesisEvidencePanel`、`ResearchPaperAnalysisView` 等前端细粒度组件合并为 `FE-WORKBENCH`。
- 过时描述特别说明：`docs/project/项目方案-完整版.md` 是 2026-07-21 历史基线，其中 `academic_chunker.py` 路径与“Agent4 依赖图谱，不可用时降级”的描述已被当前实现替代；当前分块入口是 `knowledge/chunking/ragflow_like/parsers/academic.py`，严格图谱模式不静默降级。出图时不要使用该文档的旧路径与旧降级描述。

## 待确认清单（≤5）

1. 图的最终用途与受众未说明：默认按“架构图 + 主流程图”双用途整理，组件清单服务 architecture，主流程服务 workflow；若只出其中一种，可删掉另一半。
2. “当前目录”的材料范围未指定：默认读取仓库根目录的 `README.md`、`ARCHITECTURE.md`、`docs/intro/`、`docs/project/` 与 `research_*` / `academic_*` 代码，不读 `参考秘钥`、`.env`、`backend/saves/`、`test-results/`；若你指的是某一份具体报告，请给出文件名。
3. 研究对象是否聚焦“证据可追溯主链路”：默认聚焦建库 → 检索 → 综述 → 项目 → 导出，评测/趋势/笔记等支路不画；若要“全功能能力地图”，主节点会超过 12 个，需要另建一版。
4. 外部文献源是否全部保留：默认把 Semantic Scholar、OpenAlex、Crossref 都放在 `EXT-SERVICES` 节点；若图只强调主数据源，可只保留 Semantic Scholar 与 `SEMANTIC_SCHOLAR_API_KEY`。
5. Yuxi 通用底座是否画成独立边界：默认画“运行与存储底座”边界，保留 `BE-RUNTIME` 与 `INFRA-STORE`；若只要“本仓库新增部分”视角，可去掉底座，只画科研领域服务层。
