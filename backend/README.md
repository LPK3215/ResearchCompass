# ResearchCompass Backend

FastAPI + LangGraph + ARQ backend for ResearchCompass. HTTP entrypoints live in `server/`; the reusable agent, knowledge, storage and research services live in `package/yuxi/`.

## Run

The supported development path is Docker Compose from the repository root:

```bash
docker compose up -d --build
```

The API is served on `http://localhost:5051` (container port `5050`); health check: `http://localhost:5051/api/system/health`.

## Tests and checks

```bash
uv run pytest
uv run ruff check package server scripts
uv run ruff format --check package server scripts
```

Test layers are `test/unit`, `test/integration` and `test/e2e`; see [`../docs/develop-guides/testing-guidelines.md`](../docs/develop-guides/testing-guidelines.md).

## More

- Project README: [`../README.md`](../README.md)
- Architecture map: [`../ARCHITECTURE.md`](../ARCHITECTURE.md)
- Local development: [`../docs/develop-guides/local-dev-environment.md`](../docs/develop-guides/local-dev-environment.md)

The package name remains `yuxi-workspace` for upstream compatibility; ResearchCompass-specific services use the `research_*` and `academic_*` prefixes.
