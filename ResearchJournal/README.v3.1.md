# ResearchJournal v3.1 · 源码教学补丁

这个包是给**已经安装并运行 v3** 的原位升级包，修复“版本记录里没有展示真实代码”的问题。

## 为什么之前没有代码？

- 你上传的两篇 `01-版本记录/*.md` 没有 `generator: ResearchJournal-v3`，由旧版生成。
- v3 原本只在 **新快照** 时调用 `source_comparison()`，把代码附在笔记靠后的地方；而 `revise` 仅重写旧文字，不读取快照，更不可能恢复已经丢失的旧版代码。
- v2/v3 仅保存一个可被覆写的 `state/baseline.json.gz`，无法保证追溯旧版对照。

## v3.1 新行为

1. 后台 watcher 每次发现值得记录的源代码变化，会在 `state/snapshots/<sha256>.json.gz` **追加保存**旧、新两份真实快照。不需要 Git，也不运行源代码/测试。
2. 新版本笔记在 `## 关键代码解读` 附近**自动插入** `修改前后源码（真实快照 + 中文阅读注释）`：旧版、新版独立代码块、所在文件与行号、关键变更行的中文解释。源码块完全按原样截取，额外教学注释放代码块外面。
3. 增加 `backfill-code` 命令：为**已有版本笔记**尝试找回历史源码。如果只有匹配该笔记的新源码快照，就只能补**新代码**，明说没有旧源码。如果两个都不可用，就什么也不改，绝不编造。
4. `revise` 生成的改版建议稿也会尝试附上可核验的真实源码；仍然不会覆盖原笔记。
5. 对旧笔记执行 `backfill-code` 时，会先在 `state/note-backups/` 保存完整原文，然后**仅追加一次**带标记的代码章节，绝不覆盖你原来手写的文字。已经锁定的笔记不会修改。

## 安装

**不要删除原来的 `ResearchJournal/`**，更不要覆盖 `state/` 和 `vault/`。把 `ResearchJournal-v3.1-Upgrade` 与 `ResearchJournal` 放在同一层：

```text
Friston-2/
├── ResearchJournal/                   # 你已经跑通的版本，原封不动保留
└── ResearchJournal-v3.1-Upgrade/      # 本升级包
    └── Apply-v3.1-Upgrade.ps1
```

从 Friston-2 根目录运行：

```powershell
conda activate photorobot
Set-ExecutionPolicy -Scope Process Bypass
.\ResearchJournal-v3.1-Upgrade\Apply-v3.1-Upgrade.ps1
```

脚本会停止旧 watcher、备份原程序、替换指定程序文件、使用已有 Windows 开机启动快捷方式的 Python 解释器重新启动。不会改 `config.json`、`inbox/`、`vault/`、`state/baseline.json.gz`。

## 让已有 RAG 笔记出现真实代码

这篇最新笔记的 `source_after` 是 `5648c3a165...`。**如果你本机当前 baseline 恰好仍匹配它**，可执行：

```powershell
python .\ResearchJournal\journal.py backfill-code "2026-10-19_161947-5648c3a165.md"
```

应该能看到**真实新版** `hybrid_search.py` 的词项过滤和加权 RRF 部分。原 v2 没有归档前一个快照，所以**只能展示新版，无法凭空还原旧版**；脚本会直接写明这一点。若 baseline 已发生变更，当前源码与该版本 SHA 对不上，则不会误贴另一版本的代码。

第一篇旧笔记同理，但没有匹配新快照时，命令会说明无法补充：

```powershell
python .\ResearchJournal\journal.py backfill-code "2026-10-19_155456-099b9e8b79.md"
```

可选：旧笔记想重新排版且包含可恢复的代码：

```powershell
python .\ResearchJournal\journal.py revise "2026-10-19_161947-5648c3a165.md"
```

## 下一次真实修改会怎样

不需要做额外操作；源代码安静约 5 分钟后，新日志自然会包含真·前后代码和中文注释。**只有第一次升级后的未来快照才能确保完整历史对照。**

如果觉得贴出的内容太短，可只在**原有** `ResearchJournal/config.json` 中调整：

```json
"code_compare_max_files": 3,
"code_compare_hunks_per_file": 3,
"code_compare_lines_per_snippet": 35
```

这只是配置片段，不要用它覆盖整个 JSON 文件；这三个字段原来就存在，修改其值即可。文件太多时仍会有上限，以免每篇日志沦为几千行源码。

## 重要边界

- 当前补丁仅能从本地真实快照**机械摘录**源代码，并给部分关键行生成保守的阅读注释；复杂算法解释仍来自 Codex 的 `关键代码解读`，需要人审阅。
- 真实 Python 代码块保持字节解码后的原始文字，不添加虚构注释，避免后续无法对应源码。
- `state/snapshots/` 含完整受监控源码，请勿公开共享；不会从未被跟踪的大型模型、原始数据库、密钥文件中自动复制。
- 只做静态代码解析与版本笔记管理；不编写/执行自动测试，不执行机器人项目源代码。
- 源码提取和布局模块语法已静态检查，**还没在你的 Windows 环境运行验证**。升级后请检查一篇真实笔记。
