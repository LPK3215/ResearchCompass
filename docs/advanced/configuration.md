# 配置系统详解

## 概述

系统采用多层配置架构，模型配置由网页界面管理，应用配置基于 Pydantic + TOML。

## 配置层级

```
代码默认值 → TOML 文件 → 环境变量
   (低)                      (高)
```

## 模型配置

由网页统一管理，详见 [模型配置](../intro/model-config.md)。

## 应用配置

配置项定义于 `backend/package/yuxi/config/app.py`，用户修改保存至 `saves/config/base.toml`。

### 配置项参考

| 字段 | 类型 | 默认值 | 说明 | 可运行时修改 |
|------|------|--------|------|------------|
| `save_dir` | str | `saves` | 保存目录，决定 `base.toml` 与线程数据的根路径 | 否（启动期只读） |
| `enable_content_guard` | bool | `False` | 是否启用内容审查（关键词/规则层） | 是 |
| `enable_content_guard_llm` | bool | `False` | 是否启用 LLM 内容审查，需配合 `content_guard_llm_model` | 是 |
| `default_model` | str | `siliconflow-cn:Pro/MiniMaxAI/MiniMax-M2.5` | 默认对话模型，格式 `provider-id:model-id` | 是 |
| `fast_model` | str | `siliconflow-cn:deepseek-ai/DeepSeek-V3` | 快速响应模型，用于轻量任务 | 是 |
| `embed_model` | str | `siliconflow-cn:BAAI/bge-m3` | 默认 Embedding 模型 | 是 |
| `reranker` | str | `siliconflow-cn:BAAI/bge-reranker-v2-m3` | 默认 Re-Ranker 模型 | 是 |
| `content_guard_llm_model` | str | `siliconflow-cn:Pro/MiniMaxAI/MiniMax-M2.5` | 内容审查使用的 LLM 模型 | 是 |
| `default_ocr_engine` | str | `rapid_ocr` | 默认 OCR 解析引擎，可选值：`disable` 或 `PROCESSOR_TYPES` 中注册的引擎 | 是 |

模型配置（`default_model`、`fast_model`、`embed_model`、`reranker`）由管理员页面统一管理，详见 [模型配置](../intro/model-config.md)。

### 修改配置

```python
from yuxi.config import config

config.default_model = "provider-id:model-id"
config.save()
```

配置会在保存 `base.toml` 后写入 Redis 快照（`yuxi:runtime_config`）。快照包含可运行时同步的公开配置字段，不包含 `_` 开头的内部属性和 `save_dir`；API/worker 进程在启动时各拉起一个后台同步线程，按 5 秒间隔从该快照刷新内存值，读取端无需感知。Redis 不可用时继续使用当前内存值。

`save_dir` 是启动期内部路径配置，不在管理员配置中展示，也不支持通过管理员配置接口、`base.toml` 或运行时 Redis 快照修改。sandbox 相关配置仍属于启动期敏感配置，运行中的已初始化组件不承诺完整热更新，修改后需要重启服务保证生效。

如果 `base.toml` 损坏，删除 `saves/config/base.toml` 后重启服务即可回到代码默认配置。

### 环境变量

环境变量与上述运行时配置是两套体系，主要用于启动期敏感配置和外部服务凭据。完整列表见 `.env.template`，关键变量包括：

| 变量名 | 用途 |
|--------|------|
| `SILICONFLOW_API_KEY` | SiliconFlow 模型服务密钥，驱动默认对话/嵌入/重排模型 |
| `TAVILY_API_KEY` | Tavily 网页搜索工具密钥，未配置则禁用 `tavily_search` |
| `SEMANTIC_SCHOLAR_API_KEY` | Semantic Scholar API 密钥，用于引用图谱同步、外部论文导入、严格图谱检索；详见 [科研罗盘指南](../intro/research-compass.md#semantic-scholar-api-key-配置) |
| `JWT_SECRET_KEY` | JWT 认证密钥 |
| `YUXI_INSTANCE_ID` | 实例标识，多实例部署时用于隔离 |
| `YUXI_CORS_ORIGINS` | CORS 允许的来源 |
| `SANDBOX_PROVIDER` | 沙盒提供者，固定为 `provisioner` |
| `SANDBOX_PROVISIONER_URL` | sandbox-provisioner 服务地址 |
| `SANDBOX_PROVISIONER_TOKEN` | sandbox-provisioner 管理接口 Bearer token |
| `SANDBOX_PROVISIONER_BACKEND` | provisioner 后端类型，`docker` 或 `kubernetes` |
| `OIDC_*` | 第三方 OIDC 登录相关配置，详见 [第三方登录认证](./third-party-auth.md) |
