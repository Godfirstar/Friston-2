# ResearchJournal v3.2：按功能里程碑记录，不再按静默时间写流水账

## 核心规则

- 平时仍然自动扫描源代码，并根据之前已经记录的版本追踪功能变更；不需要 Git commit。
- **文档、CHANGELOG、Readme、ResearchJournal 升级包不参与本版本算法总结**；默认只追踪代码文件。
- 按 `knowledge`、`npc`、`vision`、`hardware`、`skills`、`agent` 等组分别维护基线，避免不相关模块被塞进同一篇记录。
- 稳定超过 15 分钟后，Codex **先判断“本功能是否完整到足以写总结”**。判断只看代码结构和缺失项，不能代替你真实验收。
- 默认 `milestone_mode: review`：始终不自动发正式笔记；把相关改动放在待确认队列。这个模式最能彻底避免未完成工作被抢跑。
- 如要恢复无需手动确认的方式，设置 `milestone_mode: auto_conservative`：只在同一组至少 45 分钟不再修改、Codex 判断 ready 且置信度 >= 0.93、变更文件 <= 8 个时写正式笔记；并非真正证明功能完成，仍有误判可能。
- AI 评审失败时仍继续保存未发布变化，之后重试；不会立即写一篇“自动快照 AI 待补充”扰乱知识库。
- 多模块同步修改时，默认分别等待；只有明确指定两个组一起确认，才会写一篇跨模块笔记。
- **不生成测试文件、不运行测试、不修改机器人代码，也不清空旧 Obsidian 笔记。**

## 查看待记录任务

PowerShell：

```powershell
python .\ResearchJournal\journal.py status
```

会显示模块名、哪些文件改变、当前 Agent 判断、原因。无论 AI 判断是否 ready，都仅代表初步建议，不表示已经运行成功。

## 完成一个功能后（一条命令，无需 Git）

假设这一轮在做 RAG：

```powershell
python .\ResearchJournal\journal.py ready knowledge
```

如果是在同时修改知识检索和 NPC 调用链，并且它们属于**同一次迭代**：

```powershell
python .\ResearchJournal\journal.py ready "knowledge,npc"
```

这个动作只是排队一个**与当前源码签名绑定**的发布许可。后台在约 2 分钟未改变后才开始写正式笔记；如果期间代码继续变化，旧许可自动过期，避免记录半成品。若多模块均有待发布内容，`ready all` 不会自动把它们混成一篇。

如只想自动完成、不确认：见下一节。无需输入 Git commit 信息。

## 彻底自动（可选）

手动编辑现有 `ResearchJournal/config.json`，加入：

```json
"milestone_mode": "auto_conservative",
"milestone_gate_quiet_seconds": 900,
"milestone_auto_quiet_seconds": 2700,
"milestone_auto_confidence": 0.93
```

保留原有配置条目，只在 JSON 对象末尾增加合法字段，注意逗号。必须**重启 ResearchJournal watcher**让新配置生效。

不管 AI 有多聪明，凭代码 Diff 和静默时间都无法 100% 确认用户是否已经调试完成；全自动适合对偶尔误判可以接受的情况。如果真正要求不抢跑，用默认 review + 一次 ready。

## 忽略和分组规则

`milestone_guard.py` 中的 `eligible()` 和 `group_for()` 是初始分组规则。默认只追踪 `friston2/` 及根目录的 `main.py`、`preview.py`，忽略 docs、test、ResearchJournal*、临时项目等目录的变化，以及 Markdown 文档；RAG 相关 `friston2/knowledge` 按 knowledge 处理。若需要忽略另外一些文件，可在 `config.json` 里添加：

```json
"milestone_exclude_patterns": ["friston2/knowledge/obsolete_*.py"],
"milestone_include_patterns": ["my_robot_extension/**/*.py"]
```

注意 `config.json` 是旧版文件，本补丁不会替换它。这样也不会将升级包自身混入机器人的算法总结。

## 数据保存、安全和已有资料

- 升级脚本备份替换的程序文件，并保留 `state/`、`vault/`、`inbox/`、`config.json`；旧版 `baseline.json.gz` 不被覆盖。
- 新模块独立维护 `state/feature-baselines.json.gz`，在首次启动时从原有基线迁移，防止旧版增量丢失。
- `state/milestone-reviews.json` 记录 Agent 判定与原因；`state/milestone-approvals/` 存储发布申请。
- 现有版本记录及技术卡不自动合并、改写、归档；历史杂乱记录可以用原来的 `manage` 命令单独整理。
- 旧快照仍保留；发布时将所选模块的真实源码前后快照提供给笔记生成器。其余未发布模块的基线保持不变。
- Agent 对“功能完成”的判断只具有参考意义，不声称跑过测试，也不会自动验证效果提升。

## 建议验收（不用新建测试）

安装后先看 `status` 中是否按组识别了未发布代码，再等待已有的真实项目代码改动达到静默条件，确认日志中只有评审信息而没有凭空生成版本笔记。功能真的完成时可执行 `ready knowledge`，之后观察 Obsidian 生成一篇聚焦该模块的研发记录。无需专门修改源文件或生成测试代码。
