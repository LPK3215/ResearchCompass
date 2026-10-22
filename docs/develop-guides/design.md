# 界面设计规范

> ResearchCompass 基于开源智能体框架 ResearchCompass 二次开发，本文档定义其前端界面的基础设计规范，适用于 `web/src` 下的新页面、新组件和现有 UI 调整。它同时面向人类开发者和 AI coding agent：修改界面前先阅读本页，优先复用现有组件、CSS 变量和交互模式。

## 1. 视觉气质

ResearchCompass 是知识库、知识图谱与 Agent 开发平台，界面应保持克制、清晰、工程化。设计服务于长时间阅读、配置、调试和数据管理，不做营销页式装饰。

核心原则：

- 功能优先：视觉层级帮助用户理解任务、状态和下一步操作，不为装饰牺牲信息密度。
- 一致优先：相似功能使用相同布局、颜色、状态和交互反馈。
- 轻量优先：用背景、边框、字号和留白建立层级，避免重阴影、夸张渐变和非功能性动效。
- 可维护优先：新增样式必须基于现有 token 和组件模式，不为单次需求引入新的设计体系。

## 2. 颜色与 Token

颜色必须优先使用 `web/src/assets/css/base.css` 和 `web/src/assets/css/base.dark.css` 中定义的 CSS 变量。不要在组件中随意新增硬编码色值；确需新增全局色值时，先补充 token 并说明用途。

### 主色

`--main-*` 和 `--main-color` 用于品牌主色与关键交互：

| Token | 使用场景 |
| --- | --- |
| `--main-color` | 主按钮、选中态、重点链接、关键图标 |
| `--main-700` / `--main-600` / `--main-500` | 主色文字、hover、active、强调状态 |
| `--main-50` / `--main-30` / `--main-10` | 主色浅背景、选中行背景、轻量提示背景 |

主色只用于表达“当前选择、主要操作、关键入口”。不要把主色当作普通装饰色铺在卡片或大面积背景上。

主色进一步拆分为“填充面”和“文字态”两组语义，浅色与暗色模式各自取到满足对比度的色值：

| Token | 使用场景 |
| --- | --- |
| `--primary-fill` / `--primary-fill-hover` / `--primary-fill-active` | 主按钮等“主色底 + 反色文字”的填充面 |
| `--primary-fill-subtle` / `--primary-fill-subtle-hover` | 选中行、浅色提示条等弱填充 |
| `--primary-text` / `--primary-text-hover` / `--primary-text-active` | 链接、主色文字、强调图标 |
| `--primary-border` | 主色描边、focus 边框 |
| `--on-primary-text` | 填充面上的前景文字（白） |

> 填充面与文字态必须分开使用：浅色模式的 `--primary-fill` 较深，暗色模式的 `--primary-fill` 较亮，
> 两者都能保证白色前景文字满足对比度。直接用 `--main-color` 同时充当背景和文字色会在暗色模式下失效。

### 中性色

`--gray-*` 是默认界面骨架，用于背景、文本、边框和分割线：

| Token | 使用场景 |
| --- | --- |
| `--gray-0` | 页面和卡片主背景 |
| `--gray-10` / `--gray-25` / `--gray-50` | 次级背景、hover 背景、弱分区背景 |
| `--gray-100` / `--gray-150` / `--gray-200` | 分割线、输入框边框、卡片边框 |
| `--gray-900` / `--gray-1000` | 标题和主要正文 |
| `--gray-600` / `--gray-500` / `--gray-400` | 辅助说明、占位文本、禁用文本 |

文本也可以使用 Ant Design 兼容语义变量：`--color-text`、`--color-text-secondary`、`--color-text-tertiary`。组件内优先选语义变量；需要更细层级时再使用 `--gray-*`。

### 交互状态色

hover、选中、激活、禁用、错误属于交互反馈，统一使用状态色，不自行拼接：

| Token | 使用场景 |
| --- | --- |
| `--state-hover-bg` / `--state-hover-bg-strong` | 行、卡片、列表项的 hover 背景 |
| `--state-hover-border` / `--state-hover-text` | hover 时的边框与文字 |
| `--state-selected-bg` / `--state-selected-border` / `--state-selected-text` | 选中项、当前激活的标签页 |
| `--state-active-bg` | 按下、active 态背景 |
| `--state-disabled-bg` / `--state-disabled-text` / `--state-disabled-border` | 禁用态；禁用态不再响应 hover 强反馈 |
| `--state-error-bg` / `--state-error-border` / `--state-error-text` | 错误态容器 |

背景、边框、填充、文本也提供了一组与 Ant Design 同名的语义别名，跨组件保持一致：

`--color-bg-page`（页面背景）、`--color-bg-container`（卡片/容器）、`--color-bg-elevated`（浮层）、`--color-bg-subtle`（次级分区）、`--color-bg-muted`（弱提示）；
`--color-border`、`--color-border-subtle`（分割线）、`--color-border-strong`；
`--color-fill-hover`、`--color-fill-active`、`--color-fill-disabled`；
`--color-text`、`--color-text-secondary`、`--color-text-tertiary`、`--color-text-placeholder`、`--color-text-inverse`。

### 语义色

语义色只用于状态和反馈，不用于装饰：

| Token 组 | 使用场景 |
| --- | --- |
| `--color-success-*` | 成功、已完成、连接正常 |
| `--color-error-*` | 错误、危险操作、删除、失败 |
| `--color-warning-*` | 警告、待处理、需要注意但未失败 |
| `--color-info-*` | 信息提示、说明性状态 |
| `--color-accent-*` | 少量辅助强调，不能替代主色 |

状态标签建议使用浅背景 + 深文字，例如 `background: var(--color-success-50); color: var(--color-success-700);`。不要只依靠颜色传达状态，必要时配合文字或图标。

语义色只有 `10 / 50 / 100 / 500 / 700 / 900` 六档，不要引用不存在的档位（如 `--color-error-200`）。
引用未定义的 CSS 变量时，整条声明会在计算值阶段被丢弃，元素会回退到继承值或初始值——错误色、警告色
尤其容易因此“失效变黑”。新增色值前先确认 token 存在。

### 透明变体与遮罩

| Token | 使用场景 |
| --- | --- |
| `--primary-a30` / `--primary-a12` / `--primary-a06` / `--primary-a60` | 主色透明层：渐变、焦点光晕、卡片悬浮阴影 |
| `--success-a30` / `--success-a15` | 成功态透明层 |
| `--warning-a30` / `--warning-a15` | 警告态透明层 |
| `--error-a30` / `--error-a15` / `--error-a80` | 错误态透明层、错误脉冲光晕 |
| `--overlay-backdrop` / `--overlay-backdrop-strong` | 弹窗、抽屉遮罩 |
| `--surface-inverse-*` | 始终保持深色的表面：代码块、控制台、图片预览遮罩上的控件 |
| `--on-brand-*` | 品牌深色面板（登录/注册侧栏等）：固定深色，不随主题反转 |

`--light-*` / `--dark-*` 表示“叠在当前深色/浅色表面上的半透明色”，两套模式中语义会互换；
需要在两种模式下保持一致的深色表面时，请用 `--surface-inverse-*` 或 `--on-brand-*`，不要用 `--light-*`。

### 图表色

图表和统计可视化优先使用 `--chart-palette-*`。不要复用错误色、警告色做普通图表分类，避免与状态反馈混淆。

图表、图谱等在 Canvas / 绘图库中需要真实色值时，运行时从 CSS 变量读取：

```js
const getCSSVariable = (name) =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim()
```

`web/src/utils/chartColors.js` 已封装该模式，知识图谱的节点与边配色同样引用 `--chart-palette-*`，
并在主题切换时重新取色，保证暗色模式下不会残留浅色模式的硬编码色值。

### Ant Design 主题

`web/src/stores/theme.js` 在运行时读取 CSS 变量构建 Ant Design 主题配置，并由 `App.vue` 的
`a-config-provider` 注入。切换主题时先切换 `.dark` 类让 CSS 变量生效，再重建 Ant Design 主题。

因此按钮、输入框、表格、弹窗、标签、空状态、加载态等组件的基础外观会自动跟随设计令牌，
**不需要**为它们逐一覆写 CSS。需要给 Ant Design 组件指定具体色值时，从 `themeStore.currentTheme.token`
读取，不要写死 hex。

### 暗色模式

ResearchCompass 通过 `:root.dark` 覆盖同名 token。新增 UI 必须使用 CSS 变量而不是固定浅色值，并检查浅色、暗色两套表现。

新增组件时至少检查：

- 背景、卡片、输入框不出现纯白硬编码导致的暗色穿帮。
- 文本和边框在暗色模式下仍有足够对比度。
- hover、focus、disabled、selected、error 等状态在暗色模式下可辨认。
- 图表、代码块和第三方组件需要显式传入 theme 时，使用 `useThemeStore()` 的现有模式。

## 3. 字体与文本层级

全局字体栈定义在 `web/src/assets/css/main.css`，新增组件不要私自引入新字体。代码、命令、路径和技术标识可使用 monospace，优先复用现有 `@mono-font` 或系统 monospace 栈。

`main.css` 已提供全局排版层级（`h1`–`h6`、`p`、`strong`、`small`、`code`、`blockquote`、`ul/ol`），
组件内可用 scoped 样式覆盖字号与颜色，字重由全局规则兜底：

| Token | 值 | 用途 |
| --- | --- | --- |
| `--font-size-xs` / `--font-size-sm` / `--font-size-md` | 12 / 13 / 14px | 辅助说明、tag、小控件 |
| `--font-size-lg` | 15px | 正文默认 |
| `--font-size-xl` / `--font-size-2xl` / `--font-size-3xl` / `--font-size-4xl` | 16 / 18 / 20 / 24px | 分组标题到页面标题 |
| `--line-height-tight` / `--line-height-snug` / `--line-height-base` / `--line-height-relaxed` | 1.25 / 1.4 / 1.6 / 1.75 | 标题、控件、正文、长文 |
| `--font-weight-regular` / `--font-weight-medium` / `--font-weight-semibold` / `--font-weight-bold` | 400 / 500 / 600 / 700 | 正文、辅助、标题、强调 |

统计值、表格数字使用 `tabular-nums` 保证列对齐；长路径、无空格标识符使用 `.long-text` 或
`overflow-wrap: anywhere` 避免撑破容器。

建议层级：

| 角色 | 建议样式 | 使用场景 |
| --- | --- | --- |
| 页面标题 | 20-24px，600 | 页面主标题、弹窗主标题 |
| 分组标题 | 16-18px，600 | 卡片标题、表单分组标题 |
| 正文 | 14-15px，400 | 常规说明、列表内容 |
| 辅助说明 | 12-13px，400 | helper text、元信息、时间、统计说明 |
| 标签/状态 | 12px，500 | tag、chip、状态徽标 |
| 代码/路径 | 12-14px，monospace | 文件路径、命令、代码片段 |

文本规范：

- 标题要短，优先描述对象，不写营销式口号。
- 按钮文案使用明确动作，如“保存配置”“重新检测”“删除文件”。
- 危险操作必须让文案直接表达后果，如“删除知识库”。
- 不使用 placeholder 替代表单 label；placeholder 只做输入示例。
- 不使用负字距或按视口宽度缩放字体，避免宽屏和小屏出现不可控排版。

## 4. 组件样式

### 技术栈约束

- 包管理器：`pnpm`
- 图标库：优先使用 `lucide-vue-next`
- 样式语言：LESS
- 颜色变量：使用 `base.css` / `base.dark.css` 中的 CSS 变量
- UI 基础：复用 Ant Design Vue 和项目现有组件模式，避免为单次需求封装新组件体系

### 按钮

按钮应按操作优先级区分：

| 类型 | 使用场景 | 样式规则 |
| --- | --- | --- |
| 主按钮 | 页面主操作、确认提交 | 使用主色背景或 Ant Design primary，不在同一区域放多个主按钮 |
| 次按钮 | 返回、取消、普通操作 | 使用中性边框和浅背景，hover 只增强边框或背景 |
| 文本/链接按钮 | 表格行内操作、轻量入口 | 保持轻量，不扩大视觉权重 |
| 危险按钮 | 删除、撤销、清空等不可逆操作 | 使用 error 语义色，并配合确认弹窗或明确文案 |
| 图标按钮 | 工具栏、折叠、刷新、复制 | 使用 `lucide-icon-btn` 保证图标与文本居中 |

交互状态：

- hover 可以改变 `background`、`border-color`、`color`，不要位移或放大。
- focus 必须可见，不能移除键盘焦点样式。全局 `:focus-visible` 已提供 `2px` 主色 outline。
- disabled 使用弱化文本和背景，不绑定 hover 强反馈。
- loading 应保留按钮宽度，避免布局跳动。

Ant Design 组件的状态由 `stores/theme.js` 的主题配置统一派生，不要为单个组件覆写按钮/输入框的
hover、active、focus 样式；自研按钮才需要自行补齐上述状态。

### 输入框与表单

表单用于配置和管理任务，优先保证可读性和错误可恢复：

- 输入框背景使用 `--gray-0` 或 Ant Design 默认容器色。
- 边框使用 `--gray-150` / `--gray-200`，focus 使用主色或框架默认 focus ring。
- label 必须稳定显示，helper text 放在输入框下方。
- 错误信息使用 `--color-error-*`，并写清楚修复方式。
- 多字段表单按逻辑分组，避免把无关设置塞进同一行。

### 卡片、列表与表格

ResearchCompass 的信息界面以配置卡片、列表和表格为主，默认使用轻量分层：

- 普通卡片：`background: var(--color-bg-container); border: 1px solid var(--color-border-subtle); border-radius: var(--radius-lg);`
- 次级区域：可使用 `var(--color-bg-subtle)` / `var(--color-bg-muted)` 做轻背景。
- 点击态列表行：hover 只改变背景或边框（`--state-hover-bg`），不使用 `transform`。
- 表格行内操作保持紧凑，避免每行出现多个高权重按钮。
- 空状态要说明“当前没有什么”和“下一步可以做什么”，不要只显示图标。

圆角、阴影、层级均有 token，优先复用：

| Token | 值 | 用途 |
| --- | --- | --- |
| `--radius-xs` / `--radius-sm` / `--radius-md` | 2 / 4 / 6px | 小徽标、tag、小控件 |
| `--radius-lg` / `--radius-xl` | 8 / 12px | 卡片、面板 |
| `--radius-pill` | 999px | 状态标签、头像 |
| `--shadow-raised` | `0 1px 3px` | 轻浮层、悬浮卡片 |
| `--shadow-overlay` | `0 4px 12px` | 下拉、气泡 |
| `--shadow-popover` | `0 8px 24px` | 弹层、悬浮面板 |
| `--shadow-modal` | `0 16px 48px` | 模态框、抽屉 |
| `--z-dropdown` ~ `--z-toast` | 1000 ~ 1500 | 层级顺序 |

阴影只用于真实浮层，如弹窗、抽屉、下拉菜单、tooltip。普通卡片和列表不要用阴影制造装饰性层级。

### 状态标签

状态标签使用语义色浅背景 + 深文字：

| 状态 | 推荐 token |
| --- | --- |
| 成功/正常 | `--color-success-50` + `--color-success-700` |
| 失败/错误 | `--color-error-50` + `--color-error-700` |
| 警告/待处理 | `--color-warning-50` + `--color-warning-900` |
| 信息/运行中 | `--color-info-50` + `--color-info-700` |
| 普通/未知 | `--gray-100` + `--gray-600` |

标签可以使用 pill 圆角，但不要把 pill 形状扩散到所有按钮和卡片。

### 图标

- 常规图标尺寸使用 16px、18px 或 20px。
- 图标颜色默认继承文本色；需要强调时使用语义 token。
- 图标按钮添加 `lucide-icon-btn`，避免图标与文本或按钮中心线错位。
- 不为同一概念混用多个图标；相同操作在不同页面保持一致。

## 5. 布局与间距

间距以 4px / 8px 为基础节奏，统一使用 `--space-*` 刻度：

| Token | 值 | 场景 |
| --- | --- | --- |
| `--space-1` / `--space-2` | 4 / 8px | 图标与文本间距、表单项内部间距 |
| `--space-3` / `--space-4` | 12 / 16px | 列表行间距、弹窗内容区间距 |
| `--space-5` / `--space-6` | 20 / 24px | 卡片内部 padding、页面区块间距 |
| `--space-8` / `--space-10` / `--space-12` | 32 / 40 / 48px | 页面主要区块间距 |
| `--page-padding` | 22px（随断点变化） | 页面左右内边距 |
| `--content-max-width` | 1440px | 正文最大行长容器 |
| `--header-height` | 45px | 固定头部高度，锚点滚动避让用 |

布局原则：

- 配置型页面保持适度信息密度，不做大面积营销式留白。
- 相邻操作靠近对应内容，页面级操作放在标题区或工具栏。
- 一组按钮中主操作在视觉上最明确，取消/返回等次操作弱化。
- 宽屏下限制正文行长，避免说明文字横跨整个页面。
- 小屏下优先纵向堆叠，避免强行压缩表格和表单字段。

## 6. 深度与层级

ResearchCompass 默认采用“背景 + 边框”的轻量层级：

| 层级 | 处理方式 | 使用场景 |
| --- | --- | --- |
| 页面背景 | `var(--gray-0)` 或布局已有背景 | 主页面 |
| 次级背景 | `var(--gray-10)` / `var(--gray-25)` | 分区、弱提示、列表 hover |
| 卡片边界 | `1px solid var(--gray-150)`，8px 圆角 | 配置卡片、内容块 |
| 浮层 | 框架默认阴影或轻量 `--shadow-*` | 弹窗、抽屉、dropdown、tooltip |
| 焦点 | 主色 outline / Ant Design focus ring | 键盘可达控件 |

不要在普通内容卡片上使用重阴影。只有当元素真实覆盖其他内容、需要表达浮层关系时，才允许使用阴影。

## 7. Do / Don't

### Do

- 使用 `base.css` 和 `base.dark.css` 中的 token。
- 为新增交互补齐 hover、focus、disabled、loading、empty、error 等必要状态。
- 用背景、边框、字号、间距建立层级。
- 保持浅色和暗色模式一致可用。
- 复用 `lucide-vue-next`、Ant Design Vue 和项目已有组件模式。
- 在复杂配置区保留简短说明，帮助用户理解设置影响。

### Don't

- 不要在 hover 时使用位移、放大、旋转等装饰性 transform。
- 不要使用浓重阴影装饰普通卡片。
- 不要使用夸张渐变或大面积高饱和背景。
- 不要把语义色用于纯装饰。
- 不要新增一次性 helper、样式体系或无复用价值的抽象。
- 不要硬编码浅色模式色值导致暗色模式失效。
- 不要引用未定义的 CSS 变量（会让整条声明失效）。
- 不要在同一区域放置多个同等视觉权重的主操作。

## 8. 响应式行为

响应式设计以“功能不丢失、信息不挤压”为优先：

- 小屏下表单字段纵向排列，按钮组可换行。
- 侧栏、抽屉和弹窗要保证最小宽度内内容可读。
- 表格在小屏下允许横向滚动；不要把关键字段压缩到不可读。
- 图标按钮和主要操作需要保持可点击区域，移动端目标尺寸尽量不小于 40px。
- 长文本使用省略号时，应提供 tooltip、title 或详情入口查看完整内容。
- 图谱、图表、代码块等宽内容应保留横向滚动或自适应缩放策略。

项目已统一采用以下断点（`base.css` 中 `--page-padding` 随断点变化）：

| 断点 | 页面内边距 | 说明 |
| --- | --- | --- |
| `>= 1440px` | 30px | 宽屏，正文行长由 `--content-max-width` 限制 |
| `768px - 1199px` | 18px | 平板 |
| `<= 767px` | 14px | 手机，正文降为 `--font-size-md`，表单纵向堆叠 |

移动端使用 `@media (pointer: coarse)` 将 `.lucide-icon-btn` 最小尺寸提升到 40px；
全局已适配 `prefers-reduced-motion`，在用户开启“减少动态效果”时关闭过渡与动画。

## 9. Agent Prompt Guide

AI agent 修改或生成 ResearchCompass UI 时，优先按这一节执行。

### Quick Reference

- 页面背景：`var(--color-bg-page)`
- 次级背景：`var(--color-bg-subtle)` / `var(--color-bg-muted)`
- 卡片背景：`var(--color-bg-container)`，边框 `var(--color-border-subtle)`
- 主要文本：`var(--color-text)`
- 次级文本：`var(--color-text-secondary)` / `var(--color-text-tertiary)`
- 主色填充面：`var(--primary-fill)`（配 `var(--on-primary-text)`）
- 主色文字/链接：`var(--primary-text)`
- 状态标签圆角：`var(--radius-pill)`，文字 `--font-size-xs`
- 卡片圆角：`var(--radius-lg)`（8px）
- 小控件圆角：`var(--radius-sm)` / `var(--radius-md)`（4px / 6px）
- 常规图标尺寸：`16px` / `18px` / `20px`
- 卡片 padding：`var(--space-4)` / `var(--space-5)` / `var(--space-6)`
- 页面内边距：`var(--page-padding)`
- 浮层阴影：`var(--shadow-raised)` / `var(--shadow-overlay)` / `var(--shadow-popover)` / `var(--shadow-modal)`
- 过渡时长：`--transition-duration-fast`（120ms）/ `--transition-duration-base`（180ms）
- 普通 hover：只改变背景、边框或文字颜色

### Example Prompts

实现配置卡片：

```text
实现一个 ResearchCompass 风格的配置卡片：背景使用 var(--gray-0)，边框 1px solid var(--gray-150)，圆角 8px，不加阴影。标题使用 var(--color-text)，说明文字使用 var(--color-text-secondary)。hover 只轻微改变边框或背景，不使用 transform。
```

实现工具栏按钮：

```text
实现一个工具栏按钮：优先使用 lucide-vue-next 图标，图标尺寸 16px，按钮添加 lucide-icon-btn。默认使用中性色，hover 时使用 var(--main-color) 或 var(--main-10) 强化，不位移、不放大。
```

实现状态标签：

```text
实现状态标签：成功使用 var(--color-success-50) 背景和 var(--color-success-700) 文字；错误使用 var(--color-error-50) 背景和 var(--color-error-700) 文字；警告使用 var(--color-warning-50) 背景和 var(--color-warning-900) 文字。圆角使用 999px，文字保持 12px。
```

### 实现检查清单

- 使用现有 CSS 变量，没有新增随意硬编码色值。
- 浅色和暗色模式都检查过。
- hover、focus、disabled、loading、empty、error 状态符合场景。
- 没有使用 hover 位移、放大、旋转或装饰性动画。
- 普通卡片没有使用重阴影。
- 图标来自 `lucide-vue-next`，尺寸和对齐符合现有模式。
- API 接口、组件位置、样式语言符合前端开发规范。

## 参考资料

- `web/src/assets/css/base.css`：浅色模式 token
- `web/src/assets/css/base.dark.css`：暗色模式 token
- `web/src/assets/css/main.css`：全局排版、焦点环、布局基础样式和 `lucide-icon-btn`
- `web/src/stores/theme.js`：Ant Design 主题与设计令牌的同步层
- `web/src/utils/chartColors.js`：图表调色板的运行时读取工具
- [Awesome DESIGN.md](https://github.com/VoltAgent/awesome-design-md)：面向 AI agent 的 `DESIGN.md` 样例集合
