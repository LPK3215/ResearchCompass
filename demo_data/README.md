# 演示数据（体验验证版本）

这个目录是 ResearchCompass 的「体验验证版本」数据包：把项目一路迭代过程中沉淀下来的
**原始论文、解析后的 chunk 中间结果、向量数据对应的正文与页码定位元数据、以及检索 /
筛选 / 评测 / 研究项目等业务快照**固化到仓库里，换一台机器、重新拉起一套 Docker 环境后，
用一个脚本就能把整套演示现场恢复回来。

目录里的内容全部来自真实的开发过程数据（Semantic Scholar 开放获取论文），
不是人工编造的假数据，因此可以直接用于录屏、教学和验收演示。

## 目录结构

```
demo_data/
├── README.md
├── kb_jxhcpxnqlu/                  # 一个知识库 = 一个子目录
│   ├── manifest.json              # 知识库信息 + 论文元数据 + PDF 清单（含 sha256）
│   ├── chunks.json                # 每个 chunk 的正文、页码/章节定位元数据
│   ├── business_data.json         # 检索历史、证据、筛选、评测、研究项目等业务快照
│   └── papers/
│       ├── attention-is-all-you-need.pdf
│       └── ...
└── kb_ksf23a31w7/
    └── ...
```

`manifest.json` 里的 `counts` 字段记录了每个快照的行数，便于核对导入结果。

## 导入到当前环境

前置条件：Docker 里的 `pg` / `milvus` / `minio` 已经起来，并且后端依赖已安装
（`backend/.venv` 或容器内 `python`）。

```bash
cd backend

# 导入到默认目录（仓库根目录 demo_data/），自动新建演示知识库
python scripts/import_demo_data.py

# 指定目录与知识库名称
python scripts/import_demo_data.py --data-dir ../demo_data --kb-name "演示知识库"

# 导入到指定账号（默认取系统内第一个管理员）
python scripts/import_demo_data.py --owner zwj
```

导入脚本的行为：

1. **只新增，不删除、不覆盖**目标环境已有数据；目标知识库不存在时按 `manifest` 新建。
2. **PDF 原文**上传到 MinIO 并登记为知识文件，文件状态直接置为「已索引」。
3. **chunk** 按快照原样重建（正文 + 页码定位元数据），向量用**当前环境**的 embedding 模型
   重新计算，因此不依赖导出时的解析服务与解析容器，换机器也能用。
4. **业务快照**按业务主键重新映射后写入：与目标环境冲突的主键会自动改写为新值，
   跨表引用（论文、chunk、检索批次、评测批次、项目、任务、研究）同步更新。

导入完成后，Web 端就能看到完整的论文列表、检索历史、证据、评测记录和项目进度。

## 重新导出

在已有数据的环境里重新生成数据包（**只读，不修改任何现有数据**）：

```bash
cd backend

# 导出单个知识库
python scripts/export_demo_data.py --kb-id kb_xxx

# 导出多个知识库到仓库根目录 demo_data/
python scripts/export_demo_data.py --kb-id kb_jxhcpxnqlu kb_ksf23a31w7 --output ../demo_data

# 只看将要导出的内容，不落盘
python scripts/export_demo_data.py --kb-id kb_xxx --dry-run
```

## 已知边界

- **用户账号不随包分发**。账号由 `scripts/seed_initial_users.py` 生成（导入时若系统里一个用户
  都没有会自动执行），密码以部署环境为准；业务快照里的 `uid` 会统一指向导入者。
- **研究邀请码会被重新生成**。邀请码明文不落库（数据库里只有 sha256 哈希），
  导入时无法还原原明文，只能生成新的哈希，对应的邀请码明文以导入环境为准。
- **向量不随包分发**。向量由目标环境的 embedding 模型重算，语义检索结果一致，
  但浮点值不会与源环境逐位相同。
- **`*.pdf` 已在仓库 `.gitignore` 中对 `demo_data/` 放开**，PDF 原文随仓库分发。
