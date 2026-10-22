# FAQ

## Why does evidence synthesis fail with "only verified evidence can be cited"?

The synthesis service only accepts evidence that has been recorded and verified. If the retrieval snapshot contains unverified chunks, open the evidence-validation panel, choose **Ingest and verify** (or verify each item), then regenerate the synthesis. This is intentional: the system will not turn an unverified passage into a supported claim. See [From topic to draft](docs/intro/research-workflow.md) and [ResearchCompass](docs/intro/research-compass.md).

## Why does strict graph search say "citation graph is not ready"?

`strict_hybrid_citation_graph` requires a synced academic citation graph. Run **Sync citation graph** first, then retry. The strict mode does not silently fall back to local hybrid retrieval; a missing graph is an explicit failure so results are not mislabeled as graph-enhanced. See [ResearchCompass — retrieval modes](docs/intro/research-compass.md#检索模式).

## Semantic Scholar returns 429 or sync is very slow

Semantic Scholar limits anonymous traffic to roughly one request per second per IP. Set `SEMANTIC_SCHOLAR_API_KEY` in `.env`, restart `api-dev` and `worker-dev`, and retry. Large syncs should be run ahead of a demo and the resulting graph snapshot kept with the experiment record.

## Chat or synthesis stays in "running" forever

Check that the worker is running and that the model credentials are present:

```bash
docker ps
docker logs worker-dev --tail 100
docker logs api-dev --tail 100
```

A missing worker, a missing model key or an unavailable model provider will keep a run pending. The API also reports stalled pending runs with a `worker_unavailable` error after the configured timeout.

## How do I run the checks?

```bash
make format
make lint
make test
make test-web
make docs-build
```

For the full cold-build gate on Windows:

```powershell
.\scripts\release_check.ps1
```

`make eval-baseline`, `make eval-ablation` and `make eval-synthesis` run the fixed-corpus evaluation scripts; pass `KB_ID=kb_xxx` and any extra flags through `EVAL_ARGS`.

## Does the AI research assistant execute shell commands or write arbitrary files?

No. The research assistant uses the research domain tools and does not execute system commands or write arbitrary files. Research deliverables are created through export tools and returned as downloadable artifacts. Agents that need sandbox execution use the dedicated sandbox service. See [Capabilities overview](docs/intro/capabilities.md).

## Why do the manifests say `0.11.0` while the latest tag is `v0.11.15`?

The Python and web package manifests are at `0.11.0`; the latest release tag is `v0.11.15`. The detailed release history is in [docs/develop-guides/changelog.md](docs/develop-guides/changelog.md). Use the tag for a reproducible checkout and the manifests for package metadata.

## Where do I report a bug or a security issue?

- Bugs and feature discussion: <https://github.com/LPK3215/ResearchCompass/issues> and <https://github.com/LPK3215/ResearchCompass/discussions>
- Security issues: follow [SECURITY.md](SECURITY.md); do not open a public issue.
- Contribution workflow: [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/develop-guides/contributing.md](docs/develop-guides/contributing.md).
