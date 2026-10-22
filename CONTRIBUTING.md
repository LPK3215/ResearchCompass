# Contributing

Thanks for your interest in ResearchCompass. Issues, documentation improvements, bug fixes, tests and new features are welcome.

The **single source of truth** for the contribution process is [`docs/develop-guides/contributing.md`](docs/develop-guides/contributing.md). This root file is a short entry point; the linked guide covers the full workflow. Author-specific coding rules live in [`docs/develop-guides/coding-rules.md`](docs/develop-guides/coding-rules.md) and take priority over general conventions.

## Before you start

- Search the existing [Issues](https://github.com/LPK3215/ResearchCompass/issues) first.
- For a large feature, open an issue to discuss the design and boundaries before writing code.
- Keep one PR focused on one clear problem; do not mix unrelated refactors.
- UI changes should include a screenshot or a short recording.

## Development environment

ResearchCompass is managed with Docker Compose. Run development and tests in the running containers where possible:

```bash
docker compose up -d
docker ps
docker logs api-dev --tail 100
```

`api-dev` and `web-dev` support hot reload. See [`docker-compose.yml`](docker-compose.yml) for service definitions and [`docs/develop-guides/local-dev-environment.md`](docs/develop-guides/local-dev-environment.md) for local troubleshooting.

## Checks

Run at least:

```bash
make format
make lint
make test
make test-web
```

For the full cold-build gate on Windows:

```powershell
.\scripts\release_check.ps1
```

`release_check.ps1` covers image builds, Python lock files, Ruff, backend unit/integration/e2e tests, frontend unit tests, ESLint, the production build and the VitePress documentation build.

## Branch, commit and PR

Use a clear branch name:

```bash
git checkout -b feature/amazing-feature
git checkout -b fix/chat-stream-interrupt
git checkout -b docs/update-contributing-guide
```

Use a Conventional Commit-style message:

```text
feat: add knowledge graph import flow
fix: handle interrupted chat stream
docs: update local development guide
```

Push the branch and open a Pull Request using the [PR template](.github/PULL_REQUEST_TEMPLATE.md). Describe:

- what changed;
- why it changed;
- the affected scope;
- how it was verified.

If you add or change a user-visible capability, update the relevant documentation under `docs/` and the authoritative changelog at [`docs/develop-guides/changelog.md`](docs/develop-guides/changelog.md).

## Frontend and backend notes

- Frontend lives in `web/`; use `pnpm`, keep API wrappers in `web/src/apis`, prefer `lucide-vue-next` icons, and reuse the color variables in `web/src/assets/css/base.css`.
- Backend lives in `backend/`; target Python 3.12–3.13, keep the style pythonic, and put tests under `backend/test/{unit,integration,e2e}`.
- Never commit `.env`, `.env.prod`, API keys, test accounts or private papers.

## Feedback

- Bugs: <https://github.com/LPK3215/ResearchCompass/issues>
- Feature discussion: <https://github.com/LPK3215/ResearchCompass/discussions>
- Security issues: follow [SECURITY.md](SECURITY.md); do not open a public issue.

Every contribution is appreciated.
