# ResearchCompass 科研罗盘

ResearchCompass 是一个面向学术文献检索、分析和研究证据管理的智能平台。系统在 Yuxi 多租户知识库底座上扩展了学术语义分块、混合检索、论文引用图谱、多 Agent 分析、证据综述、消融实验和匿名用户评测。

## 核心能力

- 学术语义分块：按 Abstract、Introduction、Method、Experiments、Conclusion 等章节组织论文内容，并保留页码、字符区间及公式表格元数据。
- 双模式科研检索：本地混合模式组合向量、BM25 和重排序；严格图谱模式进一步使用论文引用图谱和 PPR 扩展，图谱不可用时明确失败。检索结果会持久化到个人历史，可恢复、置顶、删除、按原配置重跑并继续生成证据综述。
- 多 Agent 论文分析：通过 LangGraph 编排结构化提取、创新点识别、方法论分析和研究空白发现四个阶段。
- 可核查证据：检索结果、分析报告和证据综述关联真实论文分块，支持回到 PDF 原文定位。
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

该脚本检查 Compose 配置、后端 Ruff、后端单元及核心集成测试、前端科研 API 契约测试、ESLint 和生产构建。依赖真实论文、模型及 Semantic Scholar 的实验结果必须另外按 [功能实现跟踪表.md](功能实现跟踪表.md) 完成人工验收。

## 项目来源

ResearchCompass 基于开源项目 [Yuxi](https://github.com/xerrors/Yuxi) 开发，并使用 LangGraph、Milvus、Neo4j、RAGFlow 分块思路、DeepAgents 等开源能力。各依赖的权利归原作者所有。本仓库继续遵循 [MIT License](LICENSE)。
