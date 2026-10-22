# ResearchJournal — Friston-3 自动研发记录 / Obsidian

> **当前入口**：本工具位于项目根目录的 `ResearchJournal/`，当前应用源码包为 `friston3/`。配置采用 `trigger_mode=timer`、`timer_interval_seconds=600`；按10分钟轮询源码变化，不要求 Git 提交。本工具不修改机器人应用源码、不编写或运行测试、不自动提交仓库。`README.v3*.md` 保留各历史版本的安装与行为说明，不替代当前配置。

## 工作原理

1. 默认每600秒检查 `friston3/`、`main.py`、`preview.py` 内允许的代码文件，忽略 `data/`、模型权重、数据集、日志、数据库、缓存以及工具自身目录。
2. 使用独立的 timer Baseline；已有历史基线时保留其未总结变化，没有旧基线时才以当前源码建立起点，不捏造“从无到有”的过程。
3. 对允许范围内的真实源码变化计算新旧快照的 `unified diff`。当前 timer 模式不使用旧版5分钟静默窗口，也不等同于 v3.3 的 Git 触发模式。
4. 将代码差异、本文件夹内的项目决策摘要、相关 ChatGPT 导出对话片段提供给 `codex exec --sandbox read-only`，要求按 Skill 整理技术决策和可追溯证据。
5. 自动更新 `vault/01-版本记录/`、`vault/02-技术卡片/` 和索引。实验指标缺失一律写 **未测量**。
6. 分析失败时保留旧基线供后续重试，不把失败草稿当成正式成果，也不以生成了一份笔记声称机器人功能已经验收通过。

## 一次性安装（Windows 10 / Windows 11）

保留现有 `ResearchJournal/state/` 与 `vault/`，仅更新本轮指定的工具源码和配置；`ResearchJournal/` 与 `main.py`、`friston3/` 同级。不要覆盖已有快照、版本笔记或对话导入记录。

仅需安装或恢复自启动时，在实际项目根目录的 Windows PowerShell 中手动执行；本轮合并不会自动安装或启动记录器：

```powershell
conda activate photorobot
codex --version
codex login     # 如果之前已经登录过，可以跳过
.\ResearchJournal\install.ps1
```

如果 PowerShell 拦截本地脚本，仅对当前终端临时放宽（无需修改系统长期设置）：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\ResearchJournal\install.ps1
```

安装脚本只添加一个**当前用户开机启动快捷方式**并启动后台进程，不需要管理员权限；不改项目根目录的 `AGENTS.md` / `.gitignore`，不会自动 `git add` / `commit` / `push`。

没有旧基线时，首次运行读取当前源码作为起点；已有基线时继续保留差异。需要手动触发一次当前 timer 处理时，可使用 `python .\ResearchJournal\journal.py once`。实际模式与周期以 `config.json` 和 `journal.py status` 为准。现有启动快捷方式 ID 保留兼容名称，不作为角色身份配置。

Obsidian → **打开文件夹作为仓库** → 选择实际项目下的 `ResearchJournal/vault`，就能看到版本日志与知识卡片。

## ChatGPT 对话能否自动关联？

### 现在已经支持

- `context/project-decisions.md`：顶部说明当前官方 Friston-3 身份、自称 Friston、`wiki:char_4093_frston` 和 `friston3/` 包路径；下方逐日历史保留原名与当时的设计取舍。
- ChatGPT 官方设置 → **Data controls / 数据控制 → Export data / 导出数据**。官方会发送包含历史对话的 ZIP 文件。
- 把下载的 ZIP **放进 `ResearchJournal/inbox/`**；本 Agent 会自动识别常见的 `conversations.json` 或编号的 conversation JSON 文件，仅筛选项目相关对话，并存放到本地 `vault/03-GPT对话/`，生成笔记时依据本轮模块自动挑选相关对话。
- 如果你已有某一次 GPT 对话的 Markdown/TXT 摘要，也能放进 `inbox/` 自动索引。
- 后续放入新导出的 ZIP，它会按对话 ID 更新相关记录，尽量避免重复。

### 目前不能保证的环节

本工具通过本地 `inbox/` 导入用户提供的历史文件，没有实现 ChatGPT 网页对话的实时同步。是否已有新的网页对话，不会自动成为源码变更或验收证据。

因此，**源码变更 → 快照 → AI 总结 → Obsidian 是自动的**；“新增 ChatGPT 网页聊天 → 本地上下文”当前是通过用户下载 ZIP 衔接一次。强行用抓 cookie 的浏览器插件/网页爬虫自动抓聊天不推荐，涉及帐号安全、隐私和兼容性。

ChatGPT 导出可能包含私人对话。工具按当前 Friston-3 及历史项目关键词筛选；保留 Friston-2 只用于查找旧记录，不定义当前身份。请勿把 `inbox/`、`state/` 或 `vault/03-GPT对话/` 上传公开仓库。忽略规则不能撤销已有 Git 跟踪。

## 目录布局

```
项目根目录/
  main.py
  friston3/
  data/
  ResearchJournal/
    journal.py
    config.json
    install.ps1
    stop.ps1
    skills/friston-research-journal/SKILL.md
    context/project-decisions.md
    context/official-links.md
    inbox/                  # 导出 ChatGPT ZIP；不要公开
    state/                  # 本地 Baseline、处理记录和日志；不要公开
    vault/                  # Obsidian 可直接打开
      00-研发总览.md
      01-版本记录/
      02-技术卡片/
      03-GPT对话/            # 自动筛选后的历史对话；不要公开
```

## 状态和控制（只有需要排查时才用）

```powershell
python .\ResearchJournal\journal.py status
Get-Content .\ResearchJournal\state\agent.log -Tail 40
.\ResearchJournal\stop.ps1
.\ResearchJournal\stop.ps1 -Uninstall  # 停止并关闭自启，保留所有记录
```

调整当前周期时编辑 `config.json` 的 `timer_interval_seconds`；默认600秒。旧 `quiet_seconds` 配置仍供历史模式使用。**Journal 不是 Git 版本控制的替代品**；源码版本与恢复仍应使用正常 Git 或备份。迁移不重写 `vault/`、`state/`、历史快照和旧版本安装说明，不把当时的 `friston2/` 路径改写成新路径。

## AI 总结质量和安全约束

- 本工具既不运行项目文件，也不自动创建或执行测试代码。`manual acceptance` 章节只给人工验收建议。
- Baseline 对比会解释修改前后算法及逻辑；**没有真实实验日志，不能凭 diff 自动声称准确率或延迟提高**。
- GPT 历史对话是决策背景，不是代码完成证据。讨论过 Hybrid RAG / GraphRAG / ACT 不等于当下已经完成。
- Codex 需要联网和已登录账户；虽然以 `--sandbox read-only` 运行，提交给模型的差异文本仍可能通过 Codex 服务处理。配置主动跳过数据、凭据、数据库，并尽量对敏感字段脱敏，但**不能保证识别代码中的所有秘密**。涉及私密仓库，请先检查 `config.json` 的白名单及排除项。
- 对于需要进一步验证的技术文档链接，本 Agent 默认仅允许使用 `context/official-links.md` 中提供的可信入口；不得捏造外链。

## 资料

- [Codex CLI 非交互命令与只读沙盒](https://learn.chatgpt.com/docs/developer-commands?surface=cli)
- [ChatGPT 官方数据导出说明](https://help.openai.com/en/articles/7260999-exporting-your-chatgpt-history-and-data)
- [Obsidian 官方文档](https://help.obsidian.md/)
