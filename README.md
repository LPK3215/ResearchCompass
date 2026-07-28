# ResearchCompass 科研罗盘

ResearchCompass 是我面向学术文献检索、分析和研究证据管理设计并实现的科研智能体平台。本系统在开源智能体框架 Yuxi 之上，针对科研领域二次开发，实现了学术语义分块、混合检索、论文引用图谱、多 Agent 分析、证据综述、消融实验和匿名用户评测等科研业务能力；Yuxi 提供多租户知识库、智能体运行态与工具体系等通用底座，本仓库在此基础上聚焦科研场景的流程、工具与价值设计。

## 核心能力

- 对话式研究 Copilot：完整模式登录后直接进入 ResearchCompass，AI 研究助手自动获得当前知识库、研究项目、工作区和选中对象上下文，并通过我设计的受权限与审批约束的领域工具执行项目规划、论文检索、证据综述和成果归集；所有结果继续写回结构化工作台。Lite 模式不加载研究依赖，默认进入通用助手。
- 学术语义分块：我针对论文结构按 Abstract、Introduction、Method、Experiments、Conclusion 等章节组织内容，并保留页码、字符区间及公式表格元数据。
- 研究项目执行中心：以真实课题为单位管理有序里程碑、任务、优先级、截止日期和阻塞状态，由执行项自动计算进度与健康风险；统一归集并关联论文、检索、证据综述、分析与实验成果，支持 Markdown/DOCX 项目报告和完整活动审计。
- 双模式科研检索：本地混合模式组合向量、BM25 和重排序；严格图谱模式进一步使用论文引用图谱和 PPR 扩展，图谱不可用时明确失败。检索结果会持久化到个人历史，可恢复、置顶、删除、按原配置重跑并继续生成证据综述。
- 多 Agent 论文分析：在 Yuxi 的 LangGraph 智能体运行态之上，我编排了结构化提取、创新点识别、方法论分析和研究空白发现四个阶段。
- 可核查证据：检索结果、分析报告和证据综述均关联真实论文分块，支持回到 PDF 原文定位。
- 可复现实验：固定数据集、模型配置和语料指纹，对比分块、检索与分析策略，不根据缺失样本推断效果。
- 用户研究：提供一次性匿名邀请、SUS 问卷、结果聚合和 CSV 导出。

## 技术架构

| 层级 | 技术 |
| --- | --- |
| 前端 | Vue 3、Vite、Pinia、ECharts、G6、PDF.js |
| 后端 | FastAPI、LangGraph、ARQ |
| 检索与图谱 | Milvus、BM25、Cross-Encoder、Neo4j |
| 存储 | PostgreSQL、Redis、MinIO |
| 部署 | Docker Compose |

完整代码边界和运行链路见 [ARCHITECTURE.md](ARCHITECTURE.md)，ResearchCompass 使用说明见 [docs/intro/research-compass.md](docs/intro/research-compass.md)。

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

项目开发和测试统一在 Docker 容器中执行：

```powershell
.\scripts\release_check.ps1
```

该脚本冷构建开发与生产镜像（含完整/Lite 两种前端）、检查两级 Python 锁文件、Copilot 新增文件格式及全仓 Ruff 规则，运行后端单元、集成和 E2E 测试，并完成前端单测、ESLint、生产构建及 VitePress 文档构建。依赖真实论文及 Semantic Scholar 的实验结果仍需按 [功能实现跟踪表.md](功能实现跟踪表.md) 完成人工验收。

## 项目来源与致谢

ResearchCompass 的科研业务流程、领域工具、证据综述与实验能力由本仓库作者设计与实现；其通用智能体运行态、多租户知识库、沙盒与中间件底座来自开源项目 [Yuxi](https://github.com/xerrors/Yuxi)，并复用 LangGraph、Milvus、Neo4j、RAGFlow 分块思路、DeepAgents 等开源能力。各依赖的权利归原作者所有。本仓库继续遵循 [MIT License](LICENSE)。
