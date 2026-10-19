# ResearchJournal v3 · Obsidian 学习型研发记录

独立于 Friston-2 主项目运行；**没有 Git 提交要求，不改变任何机器人源文件，不运行或生成自动测试**。

## 核心变化

- 更容易看懂：先用 `> [!abstract] 一分钟看懂` 解释背景、目标与当前验证结论，再讲原理与代码。
- 数学公式：用 Obsidian 原生 LaTeX `$...$` / `$$...$$`；RRF 等算法有示例和符号解释。
- 真实前后代码：从本轮旧/新 **源码快照** 抽取有限的关键片段。若新建了一个文件，旧版会明确标为「不存在」。截断时注明，绝不把伪代码冒充真实旧实现。技术解释由 Codex 负责，源码摘录由 Python 本地程序负责。
- 维护而非无限堆积：技术卡按主题复用、增量追加；不会自动重写你手动写的部分。原有同义卡会保留，不悄悄删除；新卡尽量复用旧名。
- 明确模型：生成笔记的 frontmatter 和尾部记录模型、模型解析来源、Codex CLI 版本。默认不锁定型号；可在 config.json 中配置 `codex_model`。
- 旧笔记可改版：创建新的「改版建议稿」，永不直接覆盖已有正文。
- 归档、忽略、锁定：你可排除无价值的卡片和版本；忽略项记在 `state/maintenance.json`，避免下次重复生成。

## 常用指令（在 Friston-2 项目根目录执行）

查看现有状态/模型：

```powershell
python .\ResearchJournal\journal.py status
```

想把**现有旧笔记重新排版**，生成一个供核对的新稿，原笔记不变：

```powershell
python .\ResearchJournal\journal.py revise "2026-10-19_161947-5648c3a165.md"
```

生成的文件在 `ResearchJournal/vault/05-改版建议/`。**旧版旧快照可能已经不在当前 baseline 中**，所以改版工具不会假造前后的原始代码。未来新版本可以自动生成真实片段。

不想再保留某篇没价值的技术卡片，并阻止后续自动再创建同主题：

```powershell
python .\ResearchJournal\journal.py manage dismiss-card "条件式词项重合过滤.md"
```

不想保留某篇版本记录：

```powershell
python .\ResearchJournal\journal.py manage dismiss-version "2026-10-19_161947-5648c3a165.md"
```

以上两条是**安全归档**（文件移入 `vault/99-归档/`，不是永久删除），并记录忽略标记。想从 Obsidian UI 手动删除也可以，但**建议使用上述命令**，否则系统不会获知你希望长期忽略该主题。

锁定/解锁已有卡片或笔记（锁定后不再自动附加）：

```powershell
python .\ResearchJournal\journal.py manage lock-card "RRF按排名融合检索结果.md"
python .\ResearchJournal\journal.py manage unlock-card "RRF按排名融合检索结果.md"
python .\ResearchJournal\journal.py manage lock-version "2026-10-19_161947-5648c3a165.md"
python .\ResearchJournal\journal.py manage unlock-version "2026-10-19_161947-5648c3a165.md"
```

你也可以直接在 Obsidian 笔记 YAML 元数据中写 `maintenance: locked`，或者插入 `<!-- RJ:LOCKED -->`；不必通过命令锁定。

忽略一个不适合形成独立知识卡的技术主题，或取消忽略：

```powershell
python .\ResearchJournal\journal.py manage ignore-topic "词项重合过滤"
python .\ResearchJournal\journal.py manage unignore-topic "词项重合过滤"
```

取消忽略只让**后续**版本重新允许生成，不会自动恢复已经归档的文件；原文件仍在 `99-归档`，可手动检查和恢复。

## 调整模型与等待时间

沿用你已有的 `ResearchJournal/config.json`，它不会被升级脚本覆盖。下面属性均可选，不填写仍可运行：

```json
{
  "codex_model": "",
  "codex_executable": "",
  "code_compare_max_files": 3,
  "code_compare_hunks_per_file": 2,
  "code_compare_lines_per_snippet": 25,
  "skip_trivial_notes": false
}
```

其中 `codex_model` 留空代表采用 Codex CLI 的模型解析规则；不保证等于 ChatGPT 网页当前模型。若在 `~/.codex/config.toml` 找到明确 `model`，脚本会记录配置来源；CLI 日志若显式报告本次 model，则优先记真实报告的值；否则会如实标记不确定。你可以设置账号支持的具体 Codex 模型，例如先从 `codex` 自己的模型列表中确认可用名称，不必猜测。

`skip_trivial_notes: false` 默认**保留小修改日志**，让你决定是否归档。改成 `true` 允许模型将明确纯注释/排版修改标为 trivial 并跳过新笔记；这不代表你就能恢复被忽略的旧快照。

## 已知限制与安全

- 本地程序仅获取你主动导入的 ChatGPT 导出文件，以及 `context/project-decisions.md`；**不会实时同步全部 GPT 网页对话**。
- 自动记录基于连续文件快照，**不等于完整 Git 历史**。v2 旧版本通常无法从最后一份 baseline 重新恢复完整的旧前后源码。
- `state/` 中可能包含源码快照，`inbox/` 中可能包含私人聊天导出；这两处严禁公开分享。不要将包含私密项目内容的记录自动公开推送到云端。
- 模型可能误解代码或技术原理。自动生成是“待人工核对”，真实性强依赖源码、实验数据与使用者审阅。
- 目前归档不做语义级自动合并，也没有在 Obsidian 内提供可点击管理按钮；使用上面的命令或直接修改 Markdown 元数据。
- Obsidian 公式效果使用阅读视图查看，必要时将 `$$...$$` 单独放成段落；源代码 Markdown 跟普通笔记一样支持代码高亮。
- v3 自动索引仅更新标记 `RJ:INDEX:START/END` 之间的内容。v2 已有目录会原样保留，升级后的首次生成可能显示一段旧目录和一段 v3 新目录。可人工移走或清理旧目录段落，不会影响自动维护。

## v3 不需要做的事

不要求新建 Python 环境、不需要 Git commit、不需要额外 Obsidian 插件、不运行任何机器人代码。每次修改后仍等待约 5 分钟静默窗口；小改动的记录数量可自行维护。
