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
│   ├── chunks.json                # 每个 chunk 的正文、页码/章节定位元数据、分析结果
│   ├── business_data.json         # 检索历史、证据、筛选、评测、研究项目等业务快照
│   └── papers/
│       ├── attention-is-all-you-need.pdf
│       └── ...
└── kb_ksf23a31w7/
    └── ...
```

`manifest.json` 里的 `counts` 字段记录了每个快照的行数，便于核对导入结果：

| 字段 | 含义 |
| --- | --- |
| `papers` | 论文总数 |
| `papers_with_pdf` | 随包分发原文、导入后可恢复的论文数 |
| `papers_without_pdf` | 没有原文的论文数（见下方「已知边界」） |
| `chunks` | 随包分发的 chunk 数 |
| `<业务表名>` | 该业务表随包分发的行数 |

`schema_version` 表示 manifest 结构版本（当前为 2）；每篇论文还带 `chunk_count`，
`papers_without_pdf` 列表记录了取不到原文的论文及其原因。

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
3. **chunk** 按快照原样重建（正文 + 页码定位元数据 + chunk 级分析结果），
   向量用**当前环境**的 embedding 模型重新计算，因此不依赖导出时的解析服务与解析容器，
   换机器也能用。
4. **业务快照**按业务主键重新映射后写入：与目标环境冲突的主键会自动改写为新值，
   跨表引用（论文、chunk、检索批次、评测批次、项目、任务、研究）同步更新。
5. **重复导入是幂等的**：写入使用 `ON CONFLICT DO NOTHING`，已存在的行按「已存在」跳过，
   不会因为单条唯一键冲突而中断整批导入。
6. **导入后自检**：脚本逐表比对「导出快照行数」与「实际写入行数」，
   不一致时列出差异明细并以退出码 1 结束；跳过行与未能解析引用的行也会逐表报出。

导入完成后，Web 端就能看到完整的论文列表、检索历史、证据、评测记录和项目进度。

## 校验导入结果

导入脚本只保证「写进去了」，「写得对不对」由校验脚本核对：

```bash
cd backend

python scripts/verify_demo_data.py --kb-id kb_xxx
```

校验脚本**只读**，逐项核对并输出通过 / 失败结论：

1. **计数核对**：论文、文件、chunk 与每张业务表的行数与导出快照一致；
2. **文件与 chunk 状态**：文件已标记为「已索引」，文件登记的 chunk 数与实际 chunk 行数一致；
3. **chunk 元数据**：页码定位字段、论文快照与 chunk 级分析结果已按快照回填，正文不为空；
4. **跨表引用**：按 `scripts/demo_data_spec.py` 的共享约定逐列检查孤儿引用；
5. **真实检索**：对导入后的知识库跑一次实际检索，确认向量数据可用。

导入到新知识库时，包目录名与目标 `kb_id` 不同，要用 `--bundle` 指定数据包目录：

```bash
python scripts/verify_demo_data.py --kb-id kb_new --bundle kb_source
```

全部检查通过时退出码为 0，否则为 1；可用 `--query` / `--top-k` / `--search-mode`
（`vector` / `keyword` / `hybrid`）调整检索式。

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

- **只有带原文的论文才会随包分发 chunk**。论文元数据始终随包分发，但取不到 PDF 原文时
  （没有关联的知识文件，或原文存放位置不可达）该论文的 chunk 不导出，导入后也无法检索；
  这类论文记在 manifest 的 `papers_without_pdf` 里，导出时会打印警告。
- **用户账号不随包分发**。账号由 `scripts/seed_initial_users.py` 生成（导入时若系统里一个用户
  都没有会自动执行），密码以部署环境为准；业务快照里的 `uid` 会统一指向导入者。
- **研究邀请码会被重新生成**。邀请码明文不落库（数据库里只有 sha256 哈希），
  导入时无法还原原明文，只能生成新的哈希，对应的邀请码明文以导入环境为准。
- **跨知识库的引用会被置空**。指向导出范围之外的引用（如其他知识库的任务）
  在目标环境无法解析，导入时对应字段置空并在导入报告中计数。
- **向量不随包分发**。向量由目标环境的 embedding 模型重算，语义检索结果一致，
  但浮点值不会与源环境逐位相同。
- **知识图谱不随包分发**。图谱数据按知识库存放在 Neo4j 中，导入后需要重新索引生成。
- **`*.pdf` 已在仓库 `.gitignore` 中对 `demo_data/` 放开**，PDF 原文随仓库分发。
