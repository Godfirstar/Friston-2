# ResearchJournal — Friston-2 自动研发记录 / Obsidian

> **文件夹独立**：本工具所有文件都放在 `Friston-2/ResearchJournal/` 内。不改主项目源码、不写或运行测试、不执行 Git 命令、不提交仓库、不依赖 Obsidian 插件。

## 工作原理

1. 自动监听 Friston-2 允许的源码文件，每 30 秒检查一次。忽略 `data/`、模型权重、数据集、日志、数据库、缓存以及工具自身目录。
2. 第一次运行建立 **Baseline 快照**（不生成误导性的“从无到有”版本笔记）。
3. 检测到变化后，等待**连续 5 分钟没有新的代码修改**，且两次产出至少间隔 20 分钟，计算新旧快照的 `unified diff`。
4. 将代码差异、本文件夹内的项目决策摘要、相关 ChatGPT 导出对话片段提供给 `codex exec --sandbox read-only`，要求按 Skill 整理技术决策和可追溯证据。
5. 自动更新 `vault/01-版本记录/`、`vault/02-技术卡片/` 和索引。实验指标缺失一律写 **未测量**。
6. Codex 无法运行/不在线时先落地保底差异笔记，等恢复后自动重试补充 AI 解释。**记录不会因模型失败而丢失。**

## 一次性安装（Windows 10 / Windows 11）

把 ZIP 里的**整个 `ResearchJournal` 文件夹**解压到现有 Friston-2 项目目录（和 `main.py`、`friston2/` 同级）。不要把文件散放在项目根目录。

在 Windows PowerShell 中执行：

```powershell
cd C:\Users\FIRST\Desktop\Friston-2
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

**首次安装不会为现有代码生成变更记录**，而是读取当前状态作为 Baseline。日后你继续修改源码，就会自动产出笔记。不想等静默期时可以自行调用 `python .\ResearchJournal\journal.py once`，但平时无需运行。

Obsidian → **打开文件夹作为仓库** → 选择 `Friston-2/ResearchJournal/vault`，就能看到版本日志与知识卡片。

## ChatGPT 对话能否自动关联？

### 现在已经支持

- `context/project-decisions.md`：初始预置此前我们讨论过的 Friston-2 设计取舍、RAG 问题、预算/离线约束、手动验收原则。无需再手动写。
- ChatGPT 官方设置 → **Data controls / 数据控制 → Export data / 导出数据**。官方会发送包含历史对话的 ZIP 文件。
- 把下载的 ZIP **放进 `ResearchJournal/inbox/`**；本 Agent 会自动识别常见的 `conversations.json` 或编号的 conversation JSON 文件，仅筛选项目相关对话，并存放到本地 `vault/03-GPT对话/`，生成笔记时依据本轮模块自动挑选相关对话。
- 如果你已有某一次 GPT 对话的 Markdown/TXT 摘要，也能放进 `inbox/` 自动索引。
- 后续放入新导出的 ZIP，它会按对话 ID 更新相关记录，尽量避免重复。

### 目前不能保证的环节

**ChatGPT 网页对话不会自动、实时传给本地 Codex。** 截至 2026-10-19，我们没有可为个人 ChatGPT 聊天历史进行无授权实时同步的官方通用 API；官方可靠路径是用户请求并下载数据导出。单靠本地 Skills、Codex Hook 或文件监听器不能绕过此限制。

因此，**源码变更 → 快照 → AI 总结 → Obsidian 是自动的**；“新增 ChatGPT 网页聊天 → 本地上下文”当前是通过用户下载 ZIP 衔接一次。强行用抓 cookie 的浏览器插件/网页爬虫自动抓聊天不推荐，涉及帐号安全、隐私和兼容性。

ChatGPT 导出可能包含私人对话。工具只筛选 Friston-2 相关内容，但请勿把 `inbox/`、`state/` 或 `vault/03-GPT对话/` 上传公开仓库。这些路径已被子目录 `.gitignore` 忽略，但历史上已跟踪的文件仍须自行处理。

## 目录布局

```
Friston-2/
  main.py
  friston2/
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

调整静默窗口时编辑 `config.json` 的 `quiet_seconds`；默认 300 秒。想回溯的源码历史应仍由正常 Git/备份独立保存：**Journal 不是 Git 版本控制的替代品**，只会保留上一份完整代码快照、每次版本总结与追溯哈希，不提供源码恢复。

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
