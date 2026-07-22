# MEMORY.md（长期记忆）

## 环境网络约束（稳定事实）
- 当前开发机（2026-07-21 实测）：`docker.io:443` 不通（被墙/限），但 `quay.io`、`pypi.org` 可达；未配置 Docker 镜像加速器。
- 可用 Docker 镜像源：道云 `https://docker.m.daocloud.io`、轩辕 `https://docker.xuanyuan.me`（443 实测通）。不通的：163/baidu/docker-cn 镜像。
- 拉取 docker.io 镜像（如 milvus/minio）前，必须在 Docker Desktop → Settings → Docker Engine 配置 `registry-mirrors`，否则 `docker compose up -d` 会因拉不到镜像失败。
- 用户预计 2026-07-23 搬家换网络（健康医学院），该网络限制大概率在新环境复现，届时重新验证镜像源可达性。

## Yuxi 开发栈启动（稳定事实）
- **必须用 `YUXI_VERSION=0.7.1.beta1` 跑 `docker compose up -d`**：`.env` 未设该变量，默认 `0.7.1` 会触发拉取/构建（慢且易败）。本地已有完整 `yuxi-*:0.7.1.beta1` 镜像，设此变量可跳过构建直接复用。
- 清理重来用 `docker compose down -v`（删容器+卷+网络）；若遇同名冲突先 `docker rm -f` 残留容器。
- `yuxi-sandbox-provisioner:0.7.1.beta1` 本地镜像有缺陷（缺 httpx + 误烤 entrypoint）。修复：`docker tag yuxi-sandbox-provisioner:0.7.1 yuxi-sandbox-provisioner:0.7.1.beta1` 覆盖后 `up -d --force-recreate sandbox-provisioner`。
- 服务端口：web 5173、api 5051（健康路径 `/api/system/health`）、Neo4j 7474/7687、Milvus 19530、MinIO 9000-9001、Postgres 5432、Redis 6379、sandbox 8002。
- **web 前端编译坑（已临时绕过）**：`web/src/utils/modelMetadata.js` 引用 `@opencode-ai/models`（package.json 已声明 0.0.15），但镜像 node_modules 缺该包；Vite 编译期解析裸模块会整页报错。已改为 `/* @vite-ignore */` + 变量说明符 + 运行期 `.catch` 降级为空 `providers`（模型元数据展示降级，不影响对话/知识库/图谱等核心功能）。**完整修复**：联网后 `pnpm install`（注意 frozen-lockfile 需 `--no-frozen-lockfile`）。当前网络下 `npm install` 该包会卡死（tarball 下载被墙），勿依赖此路。

- **功能实现度核查（2026-07-21，读代码非启动）**：用户自建 `功能实现跟踪表.md` 中多处"Yuxi框架现状/实现度"被低估，已代码核查修正并写回文档——①BM25全文检索 & 向量+BM25混合检索加权融合在 `milvus.py` 已 100% 实现（非 0%/30%）；②重排模型接口 `models/rerank.py` 已有（OpenAI/DashScope 协议，可接 BGE 类 cross-encoder），约 70%（非 0%）；③通用图谱 LLM 实体关系抽取 `graphs/extractors/llm.py` 已有（~90% 准确）。**仍需论文分析新增**：paper_analysis 4 Agent（`agents/buildin/` 仅 chatbot+subagent）、学术语义分块（`chunking/` 仅 ragflow_like）、引用关系抽取（无专门模块）、把 rerank 串联进检索流水线。
