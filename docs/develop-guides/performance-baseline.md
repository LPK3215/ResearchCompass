# ResearchCompass 性能基线

`backend/scripts/performance_baseline.py` 用于在真实运行环境中测量两个关键接口:

- `POST /api/research/databases/{kb_id}/search`（科研检索）
- `GET /api/tasks?limit=100`（任务队列查询）

脚本不会伪造或预设 P95 结果。它在每个接口上发起相同数量的请求,限制并发数,统计成功响应的 P50/P95/P99/最大延迟、状态码和传输错误;也可以通过预算参数把结果接入 CI 门禁。

## 运行

在 API 容器中执行（项目要求使用 Python 3.12 环境）:

```bash
docker compose exec api uv run python scripts/performance_baseline.py \
  --base-url http://localhost:5050 \
  --token "$RESEARCH_ACCESS_TOKEN" \
  --kb-id "$RESEARCH_KB_ID" \
  --requests 30 \
  --concurrency 5 \
  --search-p95-ms 3000 \
  --tasks-p95-ms 500
```

也可以使用环境变量 `RESEARCH_BASE_URL`、`RESEARCH_ACCESS_TOKEN`、`RESEARCH_KB_ID` 和 `RESEARCH_QUERY`;`--json` 输出适合 CI 收集的单行 JSON。预算参数是调用方的验收标准,没有提供时脚本只测量并报告,不会自行判定商业可用。

## 建议记录

每次基线应同时记录 API 镜像版本、数据库/向量库数据规模、查询文本、请求数、并发数、最大上传文件规模和外部模型配置。只有在相同数据规模和配置下比较结果,才能判断性能回归;本脚本不替代容量压测或生产监控。
