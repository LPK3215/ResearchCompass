# ResearchCompass 科研罗盘

> 面向学术文献的科研工作台：把论文变成可检索、可核查的私有文献库，再把「找证据 → 读论文 → 写综述 → 立课题 → 出报告」串成一条可追溯的流水线。
>
> **它不替你想观点，它负责让你写的每一句话都能指回一篇真实论文的某一段。**
>
> 定位口径：证据可追溯的科研工作台；证据链覆盖完整，写作、协作与投稿不在当前范围。

[文档站](https://lpk3215.github.io/ResearchCompass/) · [English](README.en.md) · [版本变更记录](docs/develop-guides/changelog.md)

## 它能干什么

| 场景 | 你得到的不是"一段回答"，而是 | 入口 |
| --- | --- | --- |
| **私有文献库** | 按 Abstract/Method/Experiments 等学术章节切分、带页码与字符区间的论文知识库 | 知识与工具管理 → 知识库 |
| **可追溯检索** | 论文 + 章节 + 原文片段 + 四路检索分数，点开回到 PDF 原文 | 科研罗盘 → 智能检索 |
| **证据约束综述** | 主题综述、核心结论、证据矛盾、研究局限、研究空白，外加"未支持结论"清单 | 科研罗盘 → 证据综述 |
| **论文分析** | 单 Agent 结构化提取，或四阶段多 Agent 拆解（创新点 / 方法学 / 空白） | 论文库 → 论文分析 |
| **课题执行中心** | 里程碑、任务、成果归集、风险登记、确定性进度与项目报告 | 科研罗盘 → 研究项目 |
| **研究地图** | 引用图谱（含两跳关系）、发表趋势与关键词演进、机会分与判断依据 | 科研罗盘 → 引用图谱 / 趋势 / 机会 |
| **AI 研究助手** | 带知识库与项目上下文、可调用领域工具、敏感操作先审批的对话式执行 | 科研罗盘右侧面板 / 科研助手页 |
| **可复现实验** | RAG 评估、检索消融、单/多 Agent A/B 盲评、SUS 匿名用户评测 | 知识库详情 / 科研罗盘 → 分析对比、用户评测 |

## 一条完整链路（写作视角）

```
建库 → 传 PDF → 读论文 → 检索找证据 → 综述（校验 → 生成 → 导出）
        ↓                                   ↓
     研究趋势 / 研究机会                研究项目（里程碑 / 任务 / 成果）
                                            ↓
                                       导出报告 → 论文各章节
                                            ↓
                                  评测 / 消融 / 盲评 / 用户评测
```

每一步点哪里、产出什么、怎么判断做对了，见 **《[从选题到成稿](./docs/intro/research-workflow.md)》**；只想快速了解能干什么，见 **《[能力全景](./docs/intro/capabilities.md)》**。

## 30 分钟上手

1. 建一个 Milvus 知识库（嵌入模型用本地 `local-embedding`），上传 5～10 篇你熟悉的论文，分块策略选 **Academic Paper**。
2. 在「智能检索」里问一个你已知答案的问题，核对返回的证据是否对得上原文。
3. 用「证据综述」生成一份小综述：失败于证据校验时点「一键录入并验证」再「重新生成」。
4. 建一个研究项目，把上面的检索与综述归集进去，导出 Markdown 报告。

## 文档地图

| 我想了解 | 看这份文档 |
| --- | --- |
| 这套系统到底能干什么 | [能力全景](docs/intro/capabilities.md) |
| 怎么用它从选题做到成稿 | [从选题到成稿](docs/intro/research-workflow.md) |
| 功能规则细节（项目/检索/综述/评测） | [科研罗盘 ResearchCompass](docs/intro/research-compass.md) |
| 装起来、跑起来 | [快速开始](docs/intro/quick-start.md) · [生产部署](docs/advanced/deployment.md) |
| 本地开发、worker、排障 | [本地开发环境](docs/develop-guides/local-dev-environment.md) |
| 模型与 API Key 怎么配 | [模型配置](docs/intro/model-config.md) · [API Key 外部集成](docs/advanced/api-key-integration.md) |
| 知识库、图谱、评估底层 | [知识库与知识图谱](docs/intro/knowledge-base.md) · [知识库评估](docs/intro/evaluation.md) |
| 智能体 / 工具 / MCP / Skills | [智能体开发](docs/agents/agents-config.md) 分组 |
| 改了什么、为什么这么改 | [版本变更记录](docs/develop-guides/changelog.md) · [规划与决策](docs/project/规划与决策.md) |
| 代码边界与运行链路 | [ARCHITECTURE.md](ARCHITECTURE.md) |

> 仓库根目录只保留入口文件（README、LICENSE、AGENTS、架构与部署编排）；立项方案、决策记录、实现跟踪表与验收记录统一归档在 [docs/project](docs/project/README.md)，不随文档站发布。

## 技术架构

| 层级 | 技术 |
| --- | --- |
| 前端 | Vue 3、Vite、Pinia、ECharts、G6、PDF.js |
| 后端 | FastAPI、LangGraph、ARQ |
| 检索与图谱 | Milvus、BM25、Cross-Encoder、Neo4j |
| 存储 | PostgreSQL、Redis、MinIO |
| 部署 | Docker Compose |

ResearchCompass 在开源智能体框架 [Yuxi](https://github.com/xerrors/Yuxi) 之上二次开发：Yuxi 提供多租户知识库、智能体运行态、沙盒与中间件等通用底座，本仓库实现学术语义分块、论文引用图谱、证据综述、项目执行中心与实验评测等科研业务。完整代码边界与运行链路见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 快速开始

前置要求：Docker Engine、Docker Compose，以及至少一个兼容 OpenAI 接口的对话模型和向量模型。

```powershell
# Windows PowerShell
.\scripts\init.ps1
docker compose up -d --build
```

```bash
# Linux / macOS
./scripts/init.sh
docker compose up -d --build
```

服务健康后访问：

- Web：<http://localhost:5173>
- API 健康检查：<http://localhost:5051/api/system/health>

完整模式默认启动 PostgreSQL、Redis、MinIO、Milvus、Neo4j、API、worker 和 Web。MinerU 与 PaddleX 属于 `all` profile，可按需要额外启动。

## 生产部署

```bash
cp .env.prod.template .env.prod
# 填写所有 REQUIRED 项及实际模型密钥
docker compose -f docker-compose.prod.yml config --quiet
docker compose -f docker-compose.prod.yml up -d --build
```

不要提交 `.env`、`.env.prod`、测试账号、论文私有数据或任何 API 密钥。详细要求见 [生产部署指南](docs/advanced/deployment.md)。

## 验证

```powershell
.\scripts\release_check.ps1
```

该脚本冷构建开发与生产镜像（含完整/Lite 两种前端）、检查两级 Python 锁文件、Copilot 新增文件格式及全仓 Ruff 规则，运行后端单元、集成和 E2E 测试，并完成前端单测、ESLint、生产构建及 VitePress 文档构建。

最近一次验收：**A–I 九组共 90 项浏览器全功能验证全部通过，未解决项 0**，含 6 条修复记录，见 [验证记录-2026-10-20](docs/project/验证记录-2026-10-20.md)。

## 项目来源与致谢

ResearchCompass 的科研业务流程、领域工具、证据综述与实验能力由本仓库作者设计与实现；其通用智能体运行态、多租户知识库、沙盒与中间件底座来自开源项目 [Yuxi](https://github.com/xerrors/Yuxi)，并复用 LangGraph、Milvus、Neo4j、RAGFlow 分块思路、DeepAgents 等开源能力。各依赖的权利归原作者所有。本仓库继续遵循 [MIT License](LICENSE)。
