# 本地开发环境启动指南

当前标准启动方式：前后端在宿主机原生运行，数据服务（PostgreSQL、Redis、Milvus、Neo4j、MinIO、etcd）以通用容器运行在 Docker 中，向量与重排模型在本地推理服务上运行。前后端不进容器。

## 服务清单

| 服务 | 位置 | 端口 | 凭据 |
|---|---|---|---|
| 后端 FastAPI | `backend/`，uv 管理，venv 在 `backend/.venv` | `127.0.0.1:5050` | 见 `.env` |
| 后台任务 worker | `backend/`，uv 管理（ARQ），与后端同机 | 无（走 Redis 队列） | 见 `.env` |
| 前端 Vite | `web/`，pnpm 管理 | `127.0.0.1:5173` | 无 |
| PostgreSQL 16 | 容器 `pg`（共享 infra） | `127.0.0.1:5432` | `postgres/postgres`，项目库 `yuxi` |
| Redis 7 | 容器 `redis`（共享 infra） | `127.0.0.1:6379` | 无 |
| MinIO | 容器 `minio`（共享 infra） | API `19000`，控制台 `19001` | `minioadmin/minioadmin` |
| etcd | 容器 `etcd`（Milvus 内部依赖） | 仅容器网络 | 无 |
| Milvus | 容器 `milvus` | `127.0.0.1:19530`，健康 `9091` | 无 |
| Neo4j | 容器 `neo4j` | Bolt `7687`，浏览器 `7474` | `neo4j/0123456789` |
| 本地向量/重排服务 | `D:\Develop\SourceCode\infra\embedding\` | `127.0.0.1:19100` | 任意占位 Key |
| 沙箱服务 sandbox-provisioner | 项目 compose `sandbox-provisioner` | `127.0.0.1:8002` | 令牌取自根目录 `.env` 的 `SANDBOX_PROVISIONER_TOKEN` |
| MinerU 解析（可选，profile=all） | 项目 compose `mineru-api` | `127.0.0.1:30001` | 无，需 NVIDIA GPU |
| PaddleX 解析（可选，profile=all） | 项目 compose `paddlex-ocr` | `127.0.0.1:8080` | 无，需 NVIDIA GPU |

共享 infra 编排在仓库外：`D:\Develop\SourceCode\infra\docker-compose.yml`，容器与卷均为通用命名（`pg`、`redis`、`minio`、`etcd`、`milvus`、`neo4j`，卷 `shared_*`），其他项目可复用，只需建自己的数据库。

> 本文的 `5050` 是宿主机直接运行 uvicorn 的端口；Compose 模式将宿主 `5051` 映射到容器 `5050`。

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
cd D:\Develop\SourceCode\agent-projects\ResearchCompass
docker compose up -d sandbox-provisioner
```

沙箱服务是智能体 `execute`、`ls` 等命令工具的运行环境，不启动时这些工具会直接报连接错误。镜像较大，首次启动前先确认镜像已拉到本地：

```powershell
docker pull enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest
```

宿主机原生运行的后端要访问容器内的沙箱服务，地址配置分两处：

- 根目录 `.env`：`SANDBOX_PROVISIONER_PUBLIC_URL=http://127.0.0.1:8002`（沙箱服务返回给后端的对外地址）、`SANDBOX_DOCKER_THREADS_HOST_PATH=<后端 saves/threads 的绝对路径>`（沙箱容器挂载的线程目录，用正斜杠）
- `backend/.env`：`SANDBOX_PROVISIONER_URL=http://127.0.0.1:8002`（后端访问沙箱服务的地址）

```powershell
cd D:\Develop\SourceCode\agent-projects\ResearchCompass\backend
uv run --no-sync --env-file ../.env uvicorn server.main:app --loop asyncio:SelectorEventLoop --host 127.0.0.1 --port 5050 --reload --reload-dir server --reload-dir package
```

```powershell
cd D:\Develop\SourceCode\agent-projects\ResearchCompass\backend
uv run --no-sync --env-file ../.env arq server.worker_main.WorkerSettings
```

```powershell
cd D:\Develop\SourceCode\agent-projects\ResearchCompass\web
pnpm dev
```

worker 必须单独起一个进程：智能体对话、文献解析、证据综述等耗时任务先写入 `agent_runs` 并投递到 Redis 队列，由 worker 消费执行。缺 worker 时接口仍返回 200，但对话会一直停在“停止回答”，因为没有进程真正消费队列。

## 健康检查

- 后端：`GET http://127.0.0.1:5050/api/system/health` → 200
- worker：`docker --context default exec redis redis-cli LLEN arq:queue` → 队列长度正常；对话能在数秒内返回回复
- 向量服务：`GET http://127.0.0.1:19100/healthz` → `status=ok` 且 dimension=384
- 沙箱服务：`GET http://127.0.0.1:8002/health` → 200 且 `backend=docker`
- Milvus：`GET http://127.0.0.1:9091/healthz` → 200
- MinIO：`GET http://127.0.0.1:19000/minio/health/live` → 200
- Neo4j：浏览器 `http://127.0.0.1:7474` 可登录

## 配置文件（均被 git 忽略）

- 根目录 `.env`：模型 API Key（`DEEPSEEK_API_KEY` 等）、MinIO/Neo4j 凭据、本地服务地址（`MINIO_URI=http://127.0.0.1:19000`、`MILVUS_URI=http://127.0.0.1:19530`、`NEO4J_URI=bolt://127.0.0.1:7687`）。后端包启动时按仓库路径自动加载（`backend/package/yuxi/__init__.py`），启动命令上的 `--env-file ../.env` 为等价写法，二者都保留。
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

- **对话发出去没有任何反应**：多半是 worker 没启动。检查 `arq server.worker_main.WorkerSettings` 进程是否存在；worker 缺席时 `POST /api/agent/runs` 仍返回 200，但 run 一直停在 `pending`，前端 SSE 流不会结束。
- **智能体命令工具报 `sandbox provisioner is unreachable`**：沙箱服务没启动或地址不对。先确认 `docker ps` 里有 `sandbox-provisioner` 且 `GET http://127.0.0.1:8002/health` 正常，再确认 `backend/.env` 的 `SANDBOX_PROVISIONER_URL` 指向 `http://127.0.0.1:8002`；改完需要重启后端与 worker。
- **后端启动报事件循环错误**：Windows 默认 Proactor 循环与 psycopg 不兼容，必须带 `--loop asyncio:SelectorEventLoop`。缺这个参数会在启动阶段报 `psycopg_pool.PoolTimeout: pool initialization incomplete after 30.0 sec`。
- **直跑时混合检索 502 / 智能体运行失败**：多半是根目录 `.env` 没被加载（`RERANK_TIMEOUT_SECONDS`、模型密钥缺失）。确认后端是按仓库路径启动的（`backend/` 目录下执行），并检查进程环境里 `RERANK_TIMEOUT_SECONDS` 是否存在。
- **5050 被残留进程占用**：`uvicorn --reload` 的父子进程可能残留，用 `Get-NetTCPConnection -LocalPort 5050` 找到 PID 后 `taskkill /F /T /PID <pid>`，确认释放再启动；多个实例并存时健康检查可能仍 200 但行为是旧代码。
- **Docker 命令报 500**：`desktop-linux` 上下文可能异常，统一使用 `docker --context default`。
- **`docker pull` 全部镜像源失败**：已通过 Docker Desktop 手动代理（`settings-store.json` 指向本机 `127.0.0.1:7897`）根治，拉取走代理直连 Docker Hub；修改代理配置后需重启 Docker Desktop，容器会自动恢复。备用方案仍可用仓库外 `D:\Develop\SourceCode\infra\bin\crane.exe` 拉取后 `docker load`。
- **文档解析引擎**：默认 `rapid_ocr`（后端进程内，无需额外服务）；可选 MinerU（`MINERU_API_URI`）与 PaddleX（`PADDLEX_URI`），两者为 GPU 容器服务，经项目 compose `--profile all` 构建启动。
- **Semantic Scholar 429**：导入与图谱同步可能限流失败，任务标记 `retryable`，稍后在任务中心重试即可。
- **embedding 报 402**：SiliconFlow 余额不足，当前链路已默认本地模型，不应再触发；若出现说明有配置回退到云端，检查系统默认配置与知识库 `embedding_model_spec`。
- **Docker Desktop 常驻内存约 0.5GB**：WSL2 底噪，不是数据库占用；不用 Docker 时可整体退出，但数据服务会一起停止。

## 资源占用参考

前后端原生约 0.8GB，六个容器约 0.9GB（Milvus 113MB、Neo4j 556MB、MinIO 78MB、etcd 30MB、pg 81MB、redis 5MB），加上 Docker Desktop 底噪合计约 2.2GB。
