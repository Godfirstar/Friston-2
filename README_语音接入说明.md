# Friston-2 × StackChan v0.5：NPC 的回复从栈酱扬声器播放

## 本次改动（无需再刷 Arduino）

你已经验证：v0.4.1 ACK128 固件返回 `FRISTON2_STACKCHAN_V4_AUDIO_PCM16_16K_ACK128`，离线中文 TTS 生成成功，并收到 `AUDIO DONE`。

本补丁复用你之前已正常工作的全部硬件链路，**不包含 Arduino 固件，不修改 SO101/RAG/模型**。

- `main.py`：基于前一版 **v0.3 Agent 联动的 main.py**，在 NPC 正常回复后调用自动语音播放；其他行为保持原样。你的电脑如果修改过 `main.py`，务必先备份和比较差异。
- `stackchan_integration/face_events.py`：保留 v0.3 表情管理，并增加同一后台线程的 PCM 播放排队。只有这个线程能打开 COM10，避免两个 Python 进程争抢串口。
- `stackchan_integration/voice_output.py`：将 NPC 的真实回复转交给离线 TTS，再把 PCM 交给上面的设备线程。用户始终能看到文字；TTS/硬件出错不影响 Agent。
- `stackchan_integration/tts_export.py`：在 `friston_tts` 环境内生成 PCM 文件，按短句拆分，最大支持 500 个字符的回复。
- `stackchan_integration/local_tts.py`：沿用你在 v0.4 验证过的 sherpa-onnx 中文男声实现。

**关键：你必须保留已经成功的 v0.4.1 `stackchan_bridge.py` 和固件。不要再把旧版 v0.3 或 v0.4 的 bridge.py 覆盖回来。**

## 1. 先关闭占用 COM10 的所有程序

在所有 PowerShell 窗口退出 `python main.py`、`python -m stackchan_integration.speak`、Arduino Serial Monitor/miniterm。不要在播放时打开第二个 COM10 客户端。

## 2. 备份并解压

```powershell
conda activate photorobot
cd C:\Users\FIRST\Desktop\Friston-2

Copy-Item .\main.py .\main.pre_v05.py.bak
Copy-Item .\stackchan_integration\face_events.py .\stackchan_integration\face_events.pre_v05.py.bak
Copy-Item .\stackchan_integration\local_tts.py .\stackchan_integration\local_tts.pre_v05.py.bak -ErrorAction SilentlyContinue

Expand-Archive `
  -LiteralPath "$HOME\Downloads\Friston2_StackChan_v05_voice_reply.zip" `
  -DestinationPath . -Force
```

只覆盖 `main.py`、`face_events.py`、`local_tts.py`，新增 `voice_output.py`、`tts_export.py`。`stackchan_bridge.py` 与 ESP32 固件完全保留 v0.4.1。

> 强烈建议先对比 `main.py` 旧文件：该补丁基于之前发给你的 v0.3 main.py。你在那之后若修改过 Agent 主流程，先手动合并差异，不要直接覆盖。

## 3. 确认两个 Python 环境

- `photorobot`：`main.py` 原来依赖环境。已经安装 `pyserial`，它负责 Agent、RAG、摄像头、栈酱唯一串口线程。
- `friston_tts`：已经下载 sherpa-onnx 中文男声模型、安装 `sherpa-onnx numpy pyserial` 的独立环境。只负责生成 PCM，不操作 COM10。

`voice_output.py` 会优先找与 photorobot 并列的 `friston_tts\python.exe`。如果找不到，可以指定：

```powershell
$env:FRISTON_TTS_PYTHON = "C:\Users\FIRST\anaconda3\envs\friston_tts\python.exe"
```

注意：**不要因为 main.py 运行在 photorobot 中，就在 photorobot 里重新安装 sherpa-onnx**。

## 4. 第一次 Agent 实际联动验收

```powershell
conda activate photorobot
cd C:\Users\FIRST\Desktop\Friston-2
$env:FRISTON_STACKCHAN_ENABLED = "1"
$env:FRISTON_STACKCHAN_PORT = "COM10"
$env:FRISTON_TTS_AUTO = "1"
$env:FRISTON_TTS_VOLUME = "60"
python main.py
```

等出现 `你:`，输入：

```text
博士，你好。
```

预期：

1. 终端仍打印 Friston-2 真实的 Qwen3 回复。
2. Agent 思考期间屏幕显示思考表情。
3. 之后打印 `[StackChanTTS] 将播放 N 段离线语音。`
4. 声音从 StackChan 机身扬声器播放，嘴巴随实际音频振幅变化（固件自动完成）。
5. 完成后恢复待机，终端重新出现 `你:`。

本版 TTS 是**在生成完完整文本之后**开始合成、播放；第一次可能有额外数秒启动/加载开销。不是边生成文本边播放的流式 TTS。

**请人工验收即可。没有新增自动测试文件，也不需要跑自动测试。**

## 5. 开关与音量

```powershell
$env:FRISTON_TTS_AUTO = "0"  # 只保留文字和表情，不合成语音
$env:FRISTON_TTS_AUTO = "1"  # 重新启用
$env:FRISTON_TTS_VOLUME = "45" # 0-160，先小音量
$env:FRISTON_STACKCHAN_ENABLED = "0" # 暂时禁用所有栈酱通信
```

设置环境变量后重启 `python main.py` 生效。

当前仅**成功的 NPC 对话**会朗读：拍照、清空记忆、失败/错误等先保留文字，避免把内部调试或操作报错也公开播出来。对话回复超过 500 字时暂时跳过语音、保留完整文字；短答可分句播报，但第一次验收建议使用简短对话。

## 6. 常见故障

- `[StackChanTTS] TTS Python not found`：设置 `FRISTON_TTS_PYTHON` 为真实的 `friston_tts\python.exe`。
- `No module named sherpa_onnx`：确认 `friston_tts` 里已安装原来的依赖，别装到 photorobot。
- `INFO ... V4_AUDIO_PCM16_16K` 而没有 `ACK128`：设备固件不是 v0.4.1，不能用 v0.5 自动语音。
- `Access is denied` 或 `PermissionError` COM10：另一程序占用串口；不要在 `main.py` 运行时调用单独的 `speak.py`。
- `StackChan unavailable`：检查 USB 线、供电和 COM 号；文字聊天应继续正常运行。
- 没有出现 `[StackChanTTS]`：此次可能走的不是 `npc_talk` 技能，或 TTS 被环境变量禁用。
- 有说话日志但没声：确认栈酱音量、供电和扬声器，复用之前已经成功的 v0.4.1 单独播放步骤排查。

## 7. 恢复 v0.3 Agent 显示功能

先退出主程序：

```powershell
cd C:\Users\FIRST\Desktop\Friston-2
Copy-Item .\main.pre_v05.py.bak .\main.py -Force
Copy-Item .\stackchan_integration\face_events.pre_v05.py.bak .\stackchan_integration\face_events.py -Force
```

不需要恢复固件：v0.4.1 仍在 ESP32-S3 上。此次没有改动 `stackchan_bridge.py`。额外生成的两个 Python 文件可以保留，只有 main.py 主动引入语音模块才会生效。

## 后续计划（不是当前补丁内容）

- v0.6：持久化 TTS 引擎进程，省去每轮启动/加载模型的延迟。
- v0.7：给 StackChan 麦克风提供离线 ASR，支持真正的双向交互。

