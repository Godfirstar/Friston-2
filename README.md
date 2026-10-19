# Friston-3 · 罗德岛智能作业平台交互项目

**非官方《明日方舟》角色交互项目。** 对话采用原作 Friston-3 身份。当前实现本地 Ollama 对话、YuNet 人脸感知、普通摄影、自动构图摄影，以及 SQLite 离线人物知识与互动记忆。机械臂递物尚未接入。工程目录和 Python 包名仍保留 `Friston-2` / `friston2`，运行显示使用 Friston-3。

v0.5 将世界观资料源替换为 [littlepangding/arknights_lore_wiki](https://github.com/littlepangding/arknights_lore_wiki)，按“人物—关系—故事—出处”重新组织。人物关系采用可追溯的有向图，存储仍使用 SQLite，支持离线查询和 JSON 子图导出。

**该 Wiki 是 LLM 阅读剧情后生成的二次总结，作者明确提醒可能存在错误与幻觉。** 本项目保留这一来源属性；“资料明确写了”不等于“已核对游戏原文”。导入的数据不自动升级为已验证原作事实。

## 运行环境与启动

- Windows / Conda，沿用已有 `photorobot` 环境；Python 3.10+。
- 本地 Ollama，默认 `qwen3.5:2b-q4_K_M`，地址 `http://127.0.0.1:11434/api/chat`。
- OpenCV、真实摄像头及 `models/face_detection_yunet_2023mar.onnx`。
- 知识导入、关系整理与检索使用 Python 标准库和 SQLite，不需要另装图数据库或增加模型。

在项目根目录的 PowerShell 中运行：

```powershell
conda activate photorobot
python main.py
```

视觉预览入口为 `python preview.py`。如果当前知识库尚未按 v0.5 构建，先执行下方的构建命令。

## 数据来源与结构

当前源文件位于 `data/external/arknights_lore_wiki`。只读取已发布的 `data/char_v3/*.txt`、`data/stories/*.txt` 和 `data/char_alias.txt`；排除 `prompt_*` 生成提示词、旧版 `data/chars` 和 `docs` 展示副本。旧版人物目录可能包含已移除的重复人物或异格页面，因此不与新版自动合并。

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

首次获取源数据时执行（当前工作目录已下载）：

```powershell
git clone --depth 1 https://github.com/littlepangding/arknights_lore_wiki.git data/external/arknights_lore_wiki
```

`data/external/` 与运行数据库不纳入项目 Git；在其他机器上需要获取源数据后重建。

退出 `main.py` 和打开知识库的 SQLite 查看器后，在项目根目录运行：

```powershell
python -m friston2.knowledge.terra_kb build --source data/external/arknights_lore_wiki
python -m friston2.knowledge.terra_kb stats
python -m friston2.knowledge.terra_kb card 可露希尔
python -m friston2.knowledge.terra_kb profile 多萝西
python -m friston2.knowledge.terra_kb relations 多萝西 --limit 12
python -m friston2.knowledge.terra_kb search 矿石病 --limit 3
python -m friston2.knowledge.terra_kb network 可露希尔 --depth 1 --limit 30 --output data/runtime/closure_graph.json
```

构建先写临时数据库，成功后再替换 `data/runtime/terra_lore.db`。构建不会清空 `data/runtime/npc_memory.db` 中的真实互动记忆。更新上游源文件后需显式重建；对话过程中不会自动联网下载、生成资料或重建全库。

旧 GameData 和 `data/retired_lore/` 目录现已不存在。旧导入器缓存、示例导出与可选精简项目见 [清理清单](docs/CLEANUP_AUDIT_2026-10-19.md)，本次已删除4个没有对应源码的旧缓存，没有删除知识库、模型、照片或用户记忆。身份迁移后须重建知识库，移除旧 Friston-2 自创节点及项目关系。

`network` 导出围绕指定人物的局部关系图，可供后续图形界面使用。深度和数量限制用于控制图的大小；它不代表全库关系已经展示完整。故事文本中的生成时间是资料元数据，不能当作游戏内事件年代。

## 对话、设定与记忆

- 保持 `qwen3.5:2b-q4_K_M`，没有换模型、训练或重建知识库。启动新进程后生效。
- 普通闲聊、人物介绍、关系评价、身份和背景问题都经过真实模型生成；旧的固定角色短答与角色兜底已退出运行路径。角色卡中的历史措辞示例不作为运行时答案或事实证据。
- 日常问候、赞美、感谢和情绪表达使用简短交流提示。事实、私人交情和真实设备执行仍有各自的证据要求。正常无句末标点、英文句点、超过三句或略超140字的完整回复不再被清空；被模型token上限截断时要求重写，不截走后面的限定。完整长答仍接受事实与身份检查。
- `知道/认识某人吗` 默认查询人物资料；明确问与本人相识、共事等才查直接关系。没有直接关系不等于不能介绍人物，也不证明从未见过。
- 别名解析区分已确定、歧义和未知。唯一中点姓名简称可查询，单字人名支持“知道／怎么看”等明确问法；`总辖`保留多个候选并由模型提出澄清。裸名字选择后按原问题重新检索，保留关系另一端；带明确新问题时保持新意图。未知人物不由其他人物的档案代答。
- 连续追问保留最多三轮完整问答，主要讨论人物相同即可，不因是否额外包含Friston而断开。换人物不发送旧人物历史；“谢谢”保留待解释的话题，明确代词衔接，词语里的“他”不会自动成为代词。
- 人物简介优先用本人的概览，评价优先用本人的概览/评价段落或有来源的自身关系。人物经历在排序和数量限制前过滤来源主体，避免其他人物挤占本人经历；明确指定活动仍可使用活动来源。历史只帮助对话衔接，不是事实证据；被拒绝的草稿不进入历史。
- 模型初稿有问题时，使用同一证据最多重写一次。再次失败返回明确的`【生成失败】`，不以固定角色台词伪装成功。规则通过仍不等于逐句事实正确。
- 真实拍照结果、现实出行/会面记录与显式记忆命令属于状态查询，可直接返回实际记录状态；它们不计为模型生成通过。用户说“你刚才拍成功了”不能写成设备记录。
- `查看证据`显示原始草稿、重写、最终结果、实体歧义、实际资料、实际历史、规则问题、`model_called`、`generation_accepted`和`output_origin`。原稿与最终稿分别评估。
- `记住：内容`保存明确的用户自述；`忘记：关键词`删除匹配用户记录与对话日志。`清空记忆`只清本次短期上下文；`清空长期记忆`删除保存的互动记录。重启不会把过去模型答复重新加载成事实。

当前上下文上限4096 token、历史最多3轮、历史预算1100估算token、生成上限240 token。表达以简短为偏好，不再因完整回复略超140字而拒绝；预算只是上限，实际送入内容见trace。

本次修改和真实测试（包括未通过项）见 [对话修复与验证](docs/DIALOGUE_REPAIR_2026-10-19.md)。[较早的验证说明](docs/DIALOGUE_CHECK_2026-10-19.md)保留为历史记录，其中旧脚本和旧日志未随当前目录保留，不能作为本次结果。

## 工程职责

| 路径 | 职责 |
| --- | --- |
| `main.py` / `preview.py` | Agent 命令行 / 视觉预览入口 |
| `friston2/config.py` | 项目绝对路径、模型和 Ollama 配置 |
| `friston2/agent.py` / `planner.py` | 指令调度 / LLM 技能选择 |
| `friston2/skills/` | 独立技能、参数验证与执行 |
| `friston2/knowledge/terra_kb.py` | 知识库构建、查询与 CLI 入口 |
| `friston2/knowledge/` | Wiki 导入、图谱存储、资料检索与证据整理 |
| `friston2/npc/` | Friston-3 人格、身份和直接关系边界与独立互动记忆 |
| `friston2/vision/` / `hardware/` | YuNet 与构图 / 真实摄像头适配器 |
| `data/knowledge/` | 有来源的 Friston-3 角色卡及构建记录 |
| `data/external/arknights_lore_wiki/` | 上游 Wiki 源文件 |
| `data/runtime/` | 生成知识库、真实互动记忆与子图导出 |
| `models/` / `photos/` | YuNet ONNX / 拍摄输出 |

`Agent → Planner → SkillManager → Skill → Hardware Adapter` 的边界保持不变。模型只能选择注册技能，参数仍由技能管理器验证，不能直接控制硬件。后续机械臂继续通过独立技能和硬件适配器接入。

离线程序契约回归：

```powershell
python -B -m unittest discover -s tests -v
```

真实对话测试（调用当前Ollama，使用独立临时记忆，不启用摄像头或其他硬件）：

```powershell
python -B -m tests.live_dialogue --live --suite reported --repeat 2
python -B -m tests.live_dialogue --live --suite all
```

`reported`覆盖原始问题及其连续追问；`all`还包含人物切换、身份和设备记录边界。输出默认保存到新的`data/runtime/live_dialogue_*.json`；也可用`--output`指定一个尚不存在的文件。测试逐轮保存完整trace、源码和数据库指纹，不覆盖旧失败。脚本完成或规则通过均不代表语义验收通过，需要查看回复及其实际资料。
