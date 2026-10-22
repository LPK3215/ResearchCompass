# 更新日志

本仓库的变更记录**只有一个真源**：[`docs/develop-guides/changelog.md`](docs/develop-guides/changelog.md)。

历史说明：本文件此前独立维护了一份 Keep a Changelog 格式的日志，最后更新停在 2026-09-08，
版本号写到 `1.0.0`，与代码实际版本（`backend/pyproject.toml` 与 `web/package.json` 均为 `0.11.0`，
最新标签 `v0.11.15`）矛盾。为避免同一事实两处维护再次跑偏，改为指向唯一真源。

- 想看每个版本改了什么：打开 [`docs/develop-guides/changelog.md`](docs/develop-guides/changelog.md)
- 想看当前版本号：直接读 `backend/pyproject.toml` 的 `version` 字段与 `git describe --tags --abbrev=0`
