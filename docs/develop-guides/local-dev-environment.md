# 本地开发环境启动指南

当前标准启动方式：前后端在宿主机原生运行，数据服务（PostgreSQL、Redis、Milvus、Neo4j、MinIO、etcd）以通用容器运行在 Docker 中，向量与重排模型在本地推理服务上运行。前后端不进容器。

## 服务清单

| 服务 | 位置 | 端口 | 凭据 |
|---|---|---|---|
| 后端 FastAPI | `backend/`，uv 管理，venv 在 `backend/.venv` | `127.0.0.1:5050` | 见 `.env` |
| 前端 Vite | `web/`，pnpm 管理 | `127.0.0.1:5173` | 无 |
| PostgreSQL 16 | 容器 `pg`（共享 infra） | `127.0.0.1:5432` | `postgres/postgres`，项目库 `yuxi` |
| Redis 7 | 容器 `redis`（共享 infra） | `127.0.0.1:6379` | 无 |
| MinIO | 容器 `minio`（共享 infra） | API `19000`，控制台 `19001` | `minioadmin/minioadmin` |
| etcd | 容器 `etcd`（Milvus 内部依赖） | 仅容器网络 | 无 |
| Milvus | 容器 `milvus` | `127.0.0.1:19530`，健康 `9091` | 无 |
| Neo4j | 容器 `neo4j` | Bolt `7687`，浏览器 `7474` | `neo4j/0123456789` |
| 本地向量/重排服务 | `D:\Develop\SourceCode\infra\embedding\` | `127.0.0.1:19100` | 任意占位 Key |

共享 infra 编排在仓库外：`D:\Develop\SourceCode\infra\docker-compose.yml`，容器与卷均为通用命名（`pg`、`redis`、`minio`、`etcd`、`milvus`、`neo4j`，卷 `shared_*`），其他项目可复用，只需建自己的数据库。

## 首次准备

1. 数据库：启动共享 infra 后在 PostgreSQL 中创建项目库 `yuxi`（表结构由后端启动时自动创建）。
2. 后端依赖：`cd backend && uv sync --no-dev`；测试环境另加 `uv sync --group test --no-dev`。
3. 前端依赖：`cd web && pnpm install`。
4. 本地向量/重排服务：`powershell -File D:\Develop\SourceCode\infra\embedding\setup-embedding.ps1`（一次性，安装 CPU 版 torch 与 sentence-transformers）。

## 每次启动（按顺序）

```powershell
docker --context default compose -f D:\Develop\SourceCode\infra\docker-compose.yml up -d
```

```powershell
powershell -File D:\Develop\SourceCode\infra\embedding\start-embedding.ps1
```

```powershell
cd D:\Develop\SourceCode\agent-projects\ResearchCompass\backend
uv run --no-sync --env-file ../.env uvicorn server.main:app --loop asyncio:SelectorEventLoop --host 127.0.0.1 --port 5050 --reload --reload-dir server --reload-dir package
```

```powershell
cd D:\Develop\SourceCode\agent-projects\ResearchCompass\web
pnpm dev
```

## 健康检查

- 后端：`GET http://127.0.0.1:5050/api/system/health` → 200
- 向量服务：`GET http://127.0.0.1:19100/healthz` → `status=ok` 且 dimension=384
- Milvus：`GET http://127.0.0.1:9091/healthz` → 200
- MinIO：`GET http://127.0.0.1:19000/minio/health/live` → 200
- Neo4j：浏览器 `http://127.0.0.1:7474` 可登录

## 配置文件（均被 git 忽略）

- 根目录 `.env`：模型 API Key（`DEEPSEEK_API_KEY` 等）、MinIO/Neo4j 凭据、本地服务地址（`MINIO_URI=http://127.0.0.1:19000`、`MILVUS_URI=http://127.0.0.1:19530`、`NEO4J_URI=bolt://127.0.0.1:7687`）。由后端启动命令的 `--env-file ../.env` 注入。
- `backend/.env`：`POSTGRES_URL`、`REDIS_URL`。
- `web/.env.local`：`VITE_API_URL=http://127.0.0.1:5050`、`VITE_MINIO_URL=http://127.0.0.1:19000`。

## 模型配置

模型供应商与模型清单存于数据库 `model_providers` 表，经管理端“模型供应商”页面或 `/api/system/model-providers` 接口管理，变更后写入 Redis 缓存。

当前生效：

- `local-embedding`：本地 19100，能力 embedding + rerank，模型 `paraphrase-multilingual-MiniLM-L12-v2`（384 维）与 `BAAI/bge-reranker-base`
- `deepseek`：`deepseek-chat`，用于查询改写、综述与分析的聊天模型
- 系统默认（管理端系统配置）：`embed_model=local-embedding:paraphrase-multilingual-MiniLM-L12-v2`，`reranker=local-embedding:BAAI/bge-reranker-base`，`default_model=deepseek:deepseek-chat`，持久化在 `backend/saves/config/base.toml`

注意：更换 embedding 模型会改变向量维度，已有知识库集合维度不匹配，需要删除知识库重建。

## 常见问题

- **后端启动报事件循环错误**：Windows 默认 Proactor 循环与 psycopg 不兼容，必须带 `--loop asyncio:SelectorEventLoop`。
- **5050 被残留进程占用**：`uvicorn --reload` 的父子进程可能残留，用 `Get-NetTCPConnection -LocalPort 5050` 找到 PID 后 `taskkill /F /T /PID <pid>`，确认释放再启动；多个实例并存时健康检查可能仍 200 但行为是旧代码。
- **Docker 命令报 500**：`desktop-linux` 上下文可能异常，统一使用 `docker --context default`。
- **`docker pull` 全部镜像源失败**：国内镜像源可能拒绝具体命名空间。替代方案是用仓库外 `D:\Develop\SourceCode\infra\bin\crane.exe` 通过本机代理拉取再 `docker load`（参考：`crane pull --format=legacy --platform=linux/amd64 <image> <tar>`）。
- **Semantic Scholar 429**：导入与图谱同步可能限流失败，任务标记 `retryable`，稍后在任务中心重试即可。
- **embedding 报 402**：SiliconFlow 余额不足，当前链路已默认本地模型，不应再触发；若出现说明有配置回退到云端，检查系统默认配置与知识库 `embedding_model_spec`。
- **Docker Desktop 常驻内存约 0.5GB**：WSL2 底噪，不是数据库占用；不用 Docker 时可整体退出，但数据服务会一起停止。

## 资源占用参考

前后端原生约 0.8GB，六个容器约 0.9GB（Milvus 113MB、Neo4j 556MB、MinIO 78MB、etcd 30MB、pg 81MB、redis 5MB），加上 Docker Desktop 底噪合计约 2.2GB。
