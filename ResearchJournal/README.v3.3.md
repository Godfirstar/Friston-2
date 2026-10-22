# ResearchJournal v3.3 — 以 Git Commit 触发真实代码分析

## 核心变化

- **仅在 `git commit` 完成后**入队；不再按文件修改时间或“静默 45 分钟”自动分析。
- **不依赖你写的 commit message**。消息可以很简短；Codex 以实际提交前/后的源码差异为证据，使用项目决策文档和已导入 GPT 历史对话补充背景。
- Git 的 `post-commit` Hook 快速保存任务并启动后台 Python worker，原则上不会让你等候 7 分钟才能完成提交。
- 自动排除 `ResearchJournal/`、文档、测试、第三方数据、缓存和大部分纯维护文件；默认只分析 `friston2/` 及根目录 `main.py`/`preview.py` 的代码文件。
- 同一次提交涉及多个业务模块时按照同一 Commit 做一份综合报告。如果它们实际上不属于一个功能，建议拆成两个 Git Commit，提交信息仍可简单写。
- 包含**真实提交前后源码片段**，不是 AI 自编代码。保留现有 Markdown 公式、技术卡管理、Obsidian 双链、版本索引。
- 每篇正式笔记 YAML 记录 `git_commit` 和第一父提交 `git_parent`，便于追溯。无需另外维护手写 Git 版本号。
- 首次 Git 根提交默认只建立提交历史基线（要分析首次提交可手动设置 `git_summarize_initial_commit: true`）；无代码变动、仅文档升级/ResearchJournal 自身改动时直接跳过，不污染知识库；AI 判为 trivial 可跳过知识笔记。
- 把失败任务保留在 `ResearchJournal/state/git-queue/pending`，最多后台自动尝试 3 次，每次失败后间隔配置的 `retry_failed_after_seconds`（通常 30 分钟）。多次失败后留存等待你手动重试，不会凭空生成“AI 待补全”笔记。
- **不会自动执行 Git add/commit/push，也不会编写或运行测试、运行机器人程序、修改应用源码。**

## 安装（现有 v3.2 原地升级）

解压后，将 `ResearchJournal-v3.3-Upgrade/` 放到 `Friston-2/` 根目录，与已有 `ResearchJournal/` 并排，不要直接解压覆盖原来的 state/vault。

确认项目已经是独立 Git 仓库（`git rev-parse --show-toplevel` 应输出 Friston-2 路径）。如果不是，**先运行 `git init` 并创建 `.gitignore` 排除数据、密钥、模型权重**，随后安装本升级包，再做首次 Commit。本升级不会自动执行 `git init` 或创建提交。

```powershell
cd C:\Users\FIRST\Desktop\Friston-2
conda activate photorobot
Set-ExecutionPolicy -Scope Process Bypass
.\ResearchJournal-v3.3-Upgrade\Apply-v3.3-Upgrade.ps1
```

安装前会检查是否已有 Git post-commit Hook 或自定义 `core.hooksPath`；冲突时拒绝覆盖并提醒手工合并。

它会：备份程序文件及原始配置、关闭旧的 Windows Startup 定时监听、把 `config.json` 的 `trigger_mode` 改为 `git`、安装 `.git/hooks/post-commit`。额外安装一个开机时**仅恢复待处理 Commit 队列**的快捷方式（没有源码扫描、没有周期性提交判断）；没有任务时马上退出。保留 `config.json` 其他设置、`state/` 的所有快照、GPT 历史导入和现有的 Obsidian 知识库。

## 用法

你正常开发、手动确认实现后，自己提交一次：

```powershell
git add -p   # 已被跟踪文件的部分改动
# 新增文件需显式 git add friston2/knowledge/new_file.py
git commit -m "完成RAG优化"
```

你不用解释算法，Commit 信息只需能区分这次开发。Git 提交后终端应看到 `[ResearchJournal] queued Git commit xxxxx`，然后后台自动分析和写 Obsidian。

**`git add -p` 的价值：**只暂存当前真正完成的改动，不要一股脑 `git add .` 把尚未完成的功能也塞进这次 Commit。RAG、NPC、视觉等不同功能尽量独立提交。

完整笔记输出至 `ResearchJournal/vault/01-版本记录/`。代码对照依据**Git 已提交版本**，不会混入提交后工作区的未完成修改。

### 查看进度和处理故障

```powershell
python .\ResearchJournal\journal.py status
# 或
python .\ResearchJournal\git_journal.py status
Get-Content .\ResearchJournal\state\agent.log -Tail 30 -Encoding UTF8
```

如果 Git Hook 创建了任务，但 Codex 暂时离线或超时，它自动重试最多 3 次并记录原因；仍失败时可手动：

```powershell
python .\ResearchJournal\git_journal.py retry all
python .\ResearchJournal\git_journal.py worker
```

如果希望对一个已有 Commit 补生成（默认不自动回顾全部历史）：

```powershell
python .\ResearchJournal\git_journal.py enqueue HEAD
python .\ResearchJournal\git_journal.py worker
```

`worker` 可能同步等待模型完成，用于排查；普通提交通过 Hook 后台运行。

### 已有笔记的维护与 GPT 导入

沿用现有的 `journal.py manage`、`revise`、`backfill-code` 和 `import` 命令。你原来的版本笔记不变；新的 Git 版本和过去的自动快照不会混作一次新里程碑。

若之后放入新的 GPT 官方导出到 `ResearchJournal/inbox/`，下一次 Git 提交时会导入并关联；也可以主动执行 `python .\ResearchJournal\journal.py import`。

### 模型与隐私

优先使用 `config.json` 的 `codex_model`，否则使用本机 Codex 模型配置。`codex exec` 使用只读沙盒，代码 Diff 会发送给 Codex 服务用于生成总结。请确保仓库不包含你不希望发送的密钥，尤其是**暂存和提交时**不要包含私密文件。

本安装只向 `.git/info/exclude` 写入私密知识库相关的本地排除规则，不修改项目根目录 `.gitignore`。如果这些文件以前已经被 Git 跟踪，**排除规则不能撤销已有跟踪**；需要用户自行清理 Git 索引或仓库历史，工具不会擅自修改历史。

### 停止/卸载

若不再需要触发，请删除属于 ResearchJournal 的 `.git/hooks/post-commit`（文件顶部标有 `RJ33_GIT_HOOK`）；不会影响既有笔记。旧 Windows Startup watcher 已在本次安装时卸载。


### Windows PowerShell 5.1 Hotfix

This package fixes a false `No Git repository found` error caused by piping
`git rev-parse` into `Select-Object -First 1` and then checking `$LASTEXITCODE`.
Both the upgrade script and the installer were updated. Existing repository
state, committed files, Obsidian notes, and Git history are not reset.

If you already extracted the original v3.3 package, overwrite the extracted
`ResearchJournal-v3.3-Upgrade` folder with this corrected package before
rerunning `Apply-v3.3-Upgrade.ps1`. This patch is not a request to `git init`.
