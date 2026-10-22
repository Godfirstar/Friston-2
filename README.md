# Friston-3 · 罗德岛智能作业平台交互项目

**非官方《明日方舟》角色交互项目。** 对话直接采用原作 Friston-3 身份，自称 Friston，绑定 `wiki:char_4093_frston`。当前实现本地 Ollama 对话、YuNet 人脸感知、普通摄影、自动构图摄影，以及 SQLite 离线人物知识与互动记忆。机械臂递物尚未接入。当前 Python 包为 `friston3/`，项目根目录以实际存放位置为准。

**当前操作入口为本文件。** 根目录其他 `README_*.md` 是 StackChan 各 v0.x 版本的历史安装或升级说明；其中旧项目名、旧压缩包名、旧路径和当时的功能状态不代表本轮身份或操作步骤。带日期的对话记录及 ResearchJournal 历史笔记保留原文。固件目录、设备协议标识及 TTS 缓存名称可能仍含 Friston2，它们是兼容标识，不定义角色身份。

v0.5 将世界观资料源替换为 [littlepangding/arknights_lore_wiki](https://github.com/littlepangding/arknights_lore_wiki)，按“人物—关系—故事—出处”重新组织。人物关系采用可追溯的有向图，存储仍使用 SQLite，支持离线查询和 JSON 子图导出。

**该 Wiki 是 LLM 阅读剧情后生成的二次总结，作者明确提醒可能存在错误与幻觉。** 本项目保留这一来源属性；“资料明确写了”不等于“已核对游戏原文”。导入的数据不自动升级为已验证原作事实。

## 运行环境与启动

- Windows / Conda，沿用已有 `photorobot` 环境；Python 3.10+。
- 本地 Ollama，路由与对话继续共用 `qwen3.5:9b`，地址 `http://127.0.0.1:11434/api/chat`；本轮不替换模型。启动日志会显示实际模型名，`FRISTON_CHAT_MODEL` 环境变量会覆盖默认值。
- OpenCV、真实摄像头及 `models/face_detection_yunet_2023mar.onnx`。
- 知识导入和关系检索使用 SQLite；语义检索沿用现有 FastEmbed、`BAAI/bge-small-zh-v1.5` 和 `terra_semantic.npz`，没有新增向量库或重建知识库。

在项目根目录的 PowerShell 中运行：

```powershell
conda activate photorobot
python main.py
```

视觉预览入口为 `python preview.py`。如果当前知识库尚未按 v0.5 构建，先执行下方的构建命令。

## 数据来源与结构

运行所需源文件位置为 `data/external/arknights_lore_wiki`。本次上传附件不含 `data/`，请沿用现有项目的角色卡、意图样本、知识库和语义索引；不要用代码包覆盖或清空它们。导入只读取已发布的 `data/char_v3/*.txt`、`data/stories/*.txt` 和 `data/char_alias.txt`；排除 `prompt_*` 生成提示词、旧版 `data/chars` 和 `docs` 展示副本。旧版人物目录可能包含已移除的重复人物或异格页面，因此不与新版自动合并。

| 内容 | 组织方式与用途 |
| --- | --- |
| 人物与别名 | 以来源 ID 识别人物，保留名称、别名及资料卡；别名存在歧义时不强行合并人物 |
| 人物栏目 | 保存简要介绍、详细介绍、相关角色、剧情高光等栏目及对应文本片段 |
| 人物关系 | 从“相关角色”栏目建立有向关联，保留对象标签、完整描述和原文位置 |
| 未解析对象 | 群体标签、歧义名称和未找到独立页面的对象保留为引用节点，便于后续核对 |
| 活动与故事 | 保存活动和干员密录总结；人物与故事之间的提及关联单独存储 |
| 来源记录 | 保存仓库版本、文件路径、内容哈希及文本位置，便于检查每条材料从哪里来 |

关系边的方向表示“这份人物资料谈到了谁”。例如 A 的“相关角色”栏目写到 B，就记录 `A → B` 及该条描述；不会把句子中的主语、宾语强行转换成“研发者”“上级”等关系。A 和 B 在同一个故事中被提及，也不会自动生成朋友、敌人或上下级关系。

详细设计、字段含义及后续扩展边界见 [知识图谱说明](docs/KNOWLEDGE_GRAPH.md)。

## 构建与查询

仅在缺少上游源数据且需要自行构建知识库时获取；已有数据不用重复下载：

```powershell
git clone --depth 1 https://github.com/littlepangding/arknights_lore_wiki.git data/external/arknights_lore_wiki
```

`data/external/` 与运行数据库不纳入项目 Git；在其他机器上需要获取源数据后重建。

退出 `main.py` 和打开知识库的 SQLite 查看器后，在项目根目录运行：

```powershell
python -m friston3.knowledge.terra_kb build --source data/external/arknights_lore_wiki
python -m friston3.knowledge.terra_kb stats
python -m friston3.knowledge.terra_kb card 可露希尔
python -m friston3.knowledge.terra_kb profile 多萝西
python -m friston3.knowledge.terra_kb relations 多萝西 --limit 12
python -m friston3.knowledge.terra_kb search 矿石病 --limit 3
python -m friston3.knowledge.terra_kb network 可露希尔 --depth 1 --limit 30 --output data/runtime/closure_graph.json
```

构建先写临时数据库，成功后再替换 `data/runtime/terra_lore.db`。构建不会清空 `data/runtime/npc_memory.db` 中的真实互动记忆。更新上游源文件后需显式重建；对话过程中不会自动联网下载、生成资料或重建全库。

本附件不包含旧清理清单，不能据此推断用户现有数据已被清理。本轮代码合并不自动重建知识库；若现有库仍包含旧 Friston-2 自创节点，应先备份并核对来源，再按知识图谱说明手动迁移。已绑定官方 Friston-3 的可用数据库可继续沿用。

`network` 导出围绕指定人物的局部关系图，可供后续图形界面使用。深度和数量限制用于控制图的大小；它不代表全库关系已经展示完整。故事文本中的生成时间是资料元数据，不能当作游戏内事件年代。

## 对话、设定与记忆

- Agentic RAG v2 默认开启：NPC 前置独立语义路由。使用同一个 FastEmbed 中文模型对 `data/knowledge/intent_examples.json` 中的意图例句编码（`CHAT / KNOWLEDGE / LIVE / FOLLOWUP`），仅当语义相似、类别间隔、近邻一致度均过阈值时直接路由；不确定、指代追问交给现有 Qwen3.5:9B 的上下文路由器。语义分类对象是**回答是否需要证据**，不是把查询与剧情段落做相似度比较。聊天只生成自然台词；事实检索保留原有实体消歧和引用边界。推理分数并非校准概率，未通过阈值不意味着某一类事实成立。
- RETRIEVE 先做数据库别名、人物栏目和有向关系检索，再按需使用已有的中文向量索引与全文检索。直接关系优先，不用共同出场或共享邻居推断朋友、亲属或上下级。关系条目保留原始对象标签、否定、时间、存疑状态及群体成员各自的行为。
- 介绍优先人物概览；能力、身份纠正、加入原因和指定活动问题使用对应栏目及故事原文。代词追问沿用当前人物，换人物则切换证据。别名歧义直接列出数据库候选，选择后按原问题重新检索；不使用人物问答白名单。
- CHAT 高置信路径通常调用一次 Qwen 生成自然台词；语义不确定时先调用 Qwen 路由，若其选择 CHAT 可采用其中的回复。闲聊的本地检查发现问题时，可在剩余预算内重写一次。事实问答先取得带主体与出处的证据，再由 Qwen 以 Friston-3 的角色口吻组织回答；涉及他人时保留原主体、关系方向、否定、时间和不确定性。只有用户明确要求“原文／逐字／直接引用”时，才允许专门的来源摘录路径。LIVE 缺乏真实数据源时明确说明，称呼不明时先澄清。
- 事实问答在同一轮预算内最多生成4稿；每个完整草稿由同一 `qwen3.5:9b` 再作一次 `evidence_review`，核对内容与实际送入的资料、纠正漏选或错选的来源，并决定接受、重写或按具体缺口补查。补充检索最多2次，沿用现有检索器；没有新增证据、查询重复或无效、检索失败或剩余预算不足时停止补查，在剩余生成预算允许时回答已有依据的部分并说明具体缺口。预算耗尽仍明确失败，不把未查到说成不存在。草稿与复核意见不是事实来源，不将草稿全文作为检索请求。没有新增常驻模型或在线 Cross-Encoder，但复核会增加模型调用与耗时；外层 Agent / Planner / SkillManager 和设备技能职责保持不变，以上次数不含外层 Planner 或启动预热。
- 同模型复核检查已送资料的一致性，随后本地规则检查来源编号、实体、身份与实际设备边界；**两者都不等于独立事实认证，`generation_accepted` 也不表示语义全部正确**。生成超时、异常或最终回复未通过检查时，返回 `success=False` 的文字诊断；不把检索片段冒充成功台词、不交给 TTS、不写入正常对话历史。证据与失败原因仍可通过“查看证据”检查。资料不足时，正常生成的回答可以坦率说明缺口；资料冲突应保留分歧。数据库仍是二次整理资料，未升级为游戏原文验证库。
- 普通聊天使用简短、温和的角色口吻，不把角色语气写成私人交情或实际行动。真实拍照、保存状态和现实会面只认执行记录；用户自述、历史台词与世界观资料不能证明设备已完成任务。
- 最多保留三轮同话题历史；历史用于衔接，不作为新事实。`查看证据`可检查实际输入、来源、最终引用编号、草稿、回退原因、模型耗时与输出路径，诊断保存在当前会话中。
- `记住：内容`保存明确的用户自述；`忘记：关键词`删除匹配的用户记录与对话日志。`清空记忆`仅清当前话题；`清空长期记忆`清除保存的互动记录。重启不把旧模型回答重新加载成世界观事实。

上下文为4096 token，历史预算1100估算 token，自然生成上限192 token。回答简短为主，但不为凑字数删去条件、否定或因果；截断输出不能冒充完整回答。启动时先预热模型并报告失败，默认保持常驻。GPU 实际驻留及推理速度以用户环境为准，本轮没有测量。可用 `FRISTON_CHAT_MODEL` 覆盖统一模型，`FRISTON_OLLAMA_KEEP_ALIVE` 覆盖驻留时间。

语义高置信路径不再额外调用 Qwen 路由；不确定分支的路由请求默认等待 3 秒。整轮模型请求的默认预算改为60秒，每次调用将剩余时间传给 `urlopen`；这是故障等待配置，不是速度目标，也不是严格墙钟截止时间。关闭 Agentic 模式同样使用此配置，不恢复隐藏的4.5秒限制。默认 fallback=`semantic` 时，路由出错的不确定请求明确失败，不自动检索无关剧情；旧版 `retrieve` 仅作为显式兼容选择。

现有 TTS 常驻子进程、异步预热、分段合成与音频播放流程保留。Qwen 请求当前仍等待完整文本，再进入 TTS；本轮没有把它改成 token 到语音的流式链路，也不以快速报错或朗读检索原文宣称提速。

### Agentic v2 配置与手动验收

配置在 `NpcTalkSkill` 创建时读取；修改环境变量后退出并重新启动 `python main.py`。继续使用 `FRISTON_CHAT_MODEL` 指定的同一个常驻模型。

| 环境变量 | 默认值 | 用途 |
| --- | --- | --- |
| `FRISTON_AGENTIC_RAG` | `1` | `0` 关闭新路由和补充检索，保留初次检索；事实回答仍最多2稿并作同模型复核 |
| `FRISTON_AGENTIC_FALLBACK` | `semantic` | Qwen 路由出错时，不确定请求明确短答而不是无依据检索；旧值 `retrieve` / `refuse` 可选（建议不使用 `retrieve`） |
| `FRISTON_AGENTIC_ROUTE_TIMEOUT` | `3` | 路由请求超时秒数，范围0.5—30 |
| `FRISTON_AGENTIC_TURN_TIMEOUT` | `60` | 整轮模型请求使用的剩余等待预算，范围1—60；不是严格墙钟截止时间 |
| `FRISTON_INTENT_ROUTER` | `1` | `0` 关闭 Embedding 意图层，保留 Qwen 上下文路由 |
| `FRISTON_INTENT_SIM_MIN` | `0.78` | 近邻最高余弦相似度下限（非概率），范围0—1 |
| `FRISTON_INTENT_MARGIN_MIN` | `0.045` | 领先类别与第二类别的 Top-3 平均相似度差下限 |
| `FRISTON_INTENT_AGREE_MIN` | `0.8` | Top-5 语义近邻中同类占比下限（0.8 表示至少4/5） |

话题中仅保存最终采用的来源及出处，用户自述和模型历史台词不成为世界观事实。部分明确的观点追问可在来源状态未变化时沿用上一轮实际采用的证据；新的事实问题仍按需检索。这里没有独立的 `REUSE` 路由或 `FRISTON_AGENTIC_REUSE_TURNS` 配置。清空短期记忆会清除话题与来源状态；重建数据库或修改角色资料后应重启进程。

在 PowerShell 中手动启动：

```powershell
conda activate photorobot
$env:FRISTON_AGENTIC_RAG = "1"
$env:FRISTON_AGENTIC_FALLBACK = "semantic"
$env:FRISTON_AGENTIC_TURN_TIMEOUT = "60"
python main.py
```

手动输入日常问候（“你好”“今天有点累”）、自我介绍、罗德岛人物事实、概念解释、观点与追问、现实经费及复合问句（“你好，陈是谁”）。检查 `decision / retrieval_calls / model_phases / output_origin / generation_accepted`：高置信闲聊应为 CHAT、零检索并得到模型台词；自我介绍应依据 Friston-3 原作证据，以 Friston 自称；普通事实应由模型自然回答，不反复播报“某某的资料提到”。事实生成的完整草稿应有 `evidence_review` 阶段；缺少标准定义句不应自动拒绝有资料支持的归纳，也不能从个案补造普遍机制。若触发补查，检查最多4稿、2次补查，重复查询或没有新增证据后应停止补查，保留能回答的部分；剩余预算耗尽时明确失败。明确知识需求进入 RETRIEVE，指代不明进入 CLARIFY。路由超时默认 REFUSE；生成失败仅显示诊断，不播放、不记入历史。LIVE 无实时来源时不猜测；混合问句的寒暄不能遮盖事实问题。再用“查看证据”核对主体、来源和模型错误。这些是手动验收目标，尚未实测通过。

`[IntentRouter]` 在启动时打印语义索引状态；首次建立 `data/runtime/intent_semantic.npz` 可多耗启动时间。意图语料改变后，重启会根据文件指纹更新小索引。`[AgenticRAG]` 输出语义近邻分数、最终决策、回退原因、调用阶段、真实耗时与最终输出来源；`retrieval_calls` 计数 `retrieve_packet` 入口调用（含补查），不代表内部搜索次数。“查看证据”保留实际输入、`sent_sources`、`used_source_ids` 和 `ollama_calls`；事实生成还记录逐稿的 `answer_attempts`、各次检索的 `retrieval_rounds`、补查次数 `additional_retrievals` 及是否实际复核的 `semantic_review_performed`。对照每稿实际送入的资料核查最终来源，不能把复核通过或规则放行当作人工语义验收。记忆命令和真实执行记录使用原有专门通路。

临时关闭：退出程序后设置 `$env:FRISTON_AGENTIC_RAG = "0"` 并重启。该模式日志为 `LEGACY`，不调用 `agentic_route`、不做补充检索；仍保留初次检索、最多2稿事实生成和同模型 `evidence_review`，并使用相同等待预算，不是撤销全部回答检查。

本轮合并仅进行源码静态审查与补丁核对；没有生成或运行自动测试，也没有连接本机 Ollama、相机或机械臂。路由准确率、P95 与回答质量尚未在用户运行环境中验收。进一步的原理、阈值和故障定位见 [语义路由 v2](docs/SEMANTIC_ROUTING_V2.md)。以下性能数字和旧 `docs/DIALOGUE_*` 均属于此前版本的记录，本轮未复核其运行材料，不能作为本次合并的测量或验收结论。

2026-10-20 此前版本本机实测（RTX 4070 Laptop 8GB，完整文本返回时间，模型已预热）：

| 验证范围 | 中位数 | P95 | 最慢 | 超过5秒 |
| --- | --- | --- | --- | --- |
| 58组数据库场景、65轮问答与追问 | 0.67秒 | 1.66秒 | 2.58秒 | 0 |
| 16轮连续混合交互中的12轮对话，包含规划器耗时 | 0.75秒 | 2.73秒 | 2.73秒 | 0 |

65轮逐条对照材料复核：54轮实质回答、2轮明确限定的部分回答、5轮合理不确认、2轮歧义澄清、1轮用户自述记忆确认、1轮闲聊；未发现错人、关系方向颠倒或无依据拒答。这是2026-10-20版本的历史回归记录，不是全世界观的准确率，也不是本轮有界复核与补查流程的验收结果。随后连续交互修复了闲聊误检索、用途问法和单字人物漏识别，最后16轮重新验证；摄影使用替身，仅验证调度与参数边界。复杂事实摘录仍可能较长，这是保留原文限定的取舍。

实测冷加载和预热约9.93秒，发生在启动阶段；访客提问前应等待启动完成。修改代码后需退出已有进程，再运行 `python main.py`。连续验证期间路由与对话始终共用同一个9B模型，未反复换载。知识库文件哈希保持不变。

## 工程职责

| 路径 | 职责 |
| --- | --- |
| `main.py` / `preview.py` | Agent 命令行 / 视觉预览入口 |
| `friston3/config.py` | 项目绝对路径、模型和 Ollama 配置 |
| `friston3/agent.py` / `planner.py` | 指令调度 / LLM 技能选择 |
| `friston3/skills/` | 独立技能、参数验证与执行 |
| `friston3/knowledge/terra_kb.py` | 知识库构建、查询与 CLI 入口 |
| `friston3/knowledge/` | Wiki 导入、图谱存储、资料检索与证据整理 |
| `friston3/npc/` | Friston-3 人格、身份和直接关系边界与独立互动记忆 |
| `friston3/vision/` / `hardware/` | YuNet 与构图 / 真实摄像头适配器 |
| `data/knowledge/` | 有来源的 Friston-3 角色卡及构建记录 |
| `data/external/arknights_lore_wiki/` | 上游 Wiki 源文件 |
| `data/runtime/` | 生成知识库、真实互动记忆与子图导出 |
| `models/` / `photos/` | YuNet ONNX / 拍摄输出 |

`Agent → Planner → SkillManager → Skill → Hardware Adapter` 的边界保持不变。模型只能选择注册技能，参数仍由技能管理器验证，不能直接控制硬件。后续机械臂继续通过独立技能和硬件适配器接入。

本次上传附件不含 `tests/`，本轮没有编写、生成或运行测试，也没有启动模型、摄像头或设备。本文件和带日期文档中的旧验证结果属于相应历史版本，不能当作本次合并的验收结论。摄影和硬件实现保持原有职责。
