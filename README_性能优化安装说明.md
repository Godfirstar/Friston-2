# Friston-2 × StackChan v0.5.2：音频传输与段间停顿优化

这次针对 2026-10-21 的真实日志做了两项优化，不是改 Qwen/RAG 或重装 TTS：

1. **4-bit IMA ADPCM 压缩**。把 16kHz/16-bit/mono PCM 发送前压缩到约 1/4 数据量，栈酱收到后解码回 PCM16，再使用现有 M5.Speaker 播放。为降低风险，**保留已验证可用的 ACK128 与 CRC32**，也支持旧版 `AUDIO` PCM16 命令。
2. **合并短段**。以前一轮中文回复合成为几段就分别上传、播放几次，每段都要重新等 USB 上传。现在将相邻 TTS 音频合并成不超过 12 秒的单条音频（`FRISTON_TTS_PACK_WAIT_S` 控制最多等待下一个短段多久），降低片段边界的长停顿。

它不会让 Qwen 再跑一遍，也没有增加新的 TTS 模型。**压缩是有损的**，语音听感可能比 PCM16 略粗糙；可一键切回 PCM16 做声音对比。

## 0. 当前版本与兼容范围

此增量补丁以**已经正常运行的 v0.4.1 ACK128 Arduino 固件 + v0.5.1 Windows 语音输出**为基础。

覆盖三个文件：

```
stackchan_integration/
  firmware/friston2_stackchan/friston2_stackchan.ino
  stackchan_bridge.py
  voice_output.py
```

其他文件（包括 `main.py`、`face_events.py`、`speak.py`、`tts_daemon.py`、`local_tts.py`、RAG、SO-101）**完全不改**。

## 1. 停止程序、备份文件并解压

请确保当前 Friston-2 项目路径仍为：

```
C:\Users\FIRST\Desktop\Friston-2
```

在 PowerShell 中运行：

```powershell
conda activate photorobot
cd C:\Users\FIRST\Desktop\Friston-2

# 关闭 python main.py、独立 speak.py、Arduino 串口监视器与 miniterm，释放 COM10

Copy-Item .\stackchan_integration\stackchan_bridge.py .\stackchan_integration\stackchan_bridge.pre_v052.py.bak
Copy-Item .\stackchan_integration\voice_output.py .\stackchan_integration\voice_output.pre_v052.py.bak
Copy-Item .\stackchan_integration\firmware\friston2_stackchan\friston2_stackchan.ino .\stackchan_integration\firmware\friston2_stackchan\friston2_stackchan.pre_v052.ino.bak

Expand-Archive -LiteralPath "$HOME\Downloads\Friston2_StackChan_v052_ADPCM.zip" -DestinationPath . -Force
```

> **备份说明：** `.bak` 是备份而不是 Arduino 代码，Arduino IDE 不会编译这几个备份文件。

## 2. 烧写升级固件（需要一次）

Arduino IDE → 文件 → 打开：

```
C:\Users\FIRST\Desktop\Friston-2\stackchan_integration\firmware\friston2_stackchan\friston2_stackchan.ino
```

保持你此前成功的设置：`M5CoreS3`、`USB CDC On Boot = Enabled`、M5GFX ≥ 0.2.27。**先点验证/编译，再点上传**。如果编译失败，保留原有设备程序，把完整报错贴给助手定位。无需修改机械臂、摄像头和其他引脚。

预期 `INFO` 是：

```
FRISTON2_STACKCHAN_V4_AUDIO_PCM16_16K_ACK128_ADPCM4
```

其中 `ACK128` 代表原来稳定的 128B 握手协议还在；`ADPCM4` 代表支持新的压缩音频。

## 3. 单独手动试听（退出 main.py 之后）

首先低音量试听，无需 TTS 模型：

```powershell
cd C:\Users\FIRST\Desktop\Friston-2
conda activate photorobot
python -m stackchan_integration.speak --port COM10 --beep --volume 40
```

然后试听中文：

```powershell
conda activate friston_tts
python -m stackchan_integration.speak --port COM10 --text "博士，我已经准备好了。" --volume 60
```

如果成功，终端会看到类似（时间以实测为准）：

```
[StackChanUSB] codec=ADPCM4 wire=...B raw=...B transfer=...s playback=...s audio=...s
AUDIO DONE - back to idle
```

**请听声音是否正常、是否有断音或过强的底噪。** 如果失败，先不要继续跑主程序。

## 4. 手动验证 Agent 连续播报

确认没有其他进程占用 COM10，然后：

```powershell
conda activate photorobot
cd C:\Users\FIRST\Desktop\Friston-2
$env:FRISTON_STACKCHAN_ENABLED = "1"
$env:FRISTON_STACKCHAN_PORT = "COM10"
$env:FRISTON_TTS_AUTO = "1"
$env:FRISTON_TTS_VOLUME = "60"
$env:FRISTON_STACKCHAN_AUDIO_CODEC = "adpcm"
$env:FRISTON_STACKCHAN_AUDIO_DIAG = "1"
$env:FRISTON_TTS_PACK_WAIT_S = "1.8"
python main.py
```

输入 `你好`，再输入 `介绍一下自己`，观察是否出现：

```
[StackChanTTS] 第 1 段已合成...
[StackChanTTS] 第 2 段已合成...
[StackChanTTS] 合并 2 段 → 播放第 1 条音频（约 11 秒）...
[StackChanUSB] codec=ADPCM4 ... transfer=... playback=... audio=...
```

以前约 11 秒自我介绍会被分成两条上传、播放；本版如两段合计少于 12 秒且 TTS 在 1.8 秒预取窗口内完成，应该合成一次设备播放。**这是设计预期，不是已在你实物上确认的结果。** 如果后一段未在预取时间内准备好，仍可能分两次；可调整：

```powershell
$env:FRISTON_TTS_PACK_WAIT_S = "2.5"
```

值越大，越容易合并短段，但首次说话可能略晚。把第一句耗时与第二句合并情况发给助手。

## 5. 音质对比、兼容性与回退

使用新版固件但希望听原始 PCM16 无损传输时，重启主程序前设置：

```powershell
$env:FRISTON_STACKCHAN_AUDIO_CODEC = "pcm"
```

这不会降低 PCM 数据量，只是用于音质对比；速度会恢复接近旧版。重新设置为 `adpcm` 即恢复压缩。

如果新压缩固件有播放异常：

1. 停止 main.py，释放 COM10。
2. 用下面命令恢复三个源文件：

```powershell
Copy-Item .\stackchan_integration\stackchan_bridge.pre_v052.py.bak .\stackchan_integration\stackchan_bridge.py -Force
Copy-Item .\stackchan_integration\voice_output.pre_v052.py.bak .\stackchan_integration\voice_output.py -Force
Copy-Item .\stackchan_integration\firmware\friston2_stackchan\friston2_stackchan.pre_v052.ino.bak .\stackchan_integration\firmware\friston2_stackchan\friston2_stackchan.ino -Force
```

3. 重新用 Arduino IDE 编译并上传恢复的 `.ino`，回到此前 v0.4.1 的稳定模式。

仅恢复 Python 两个文件（不重新刷机）也可以使用旧版 PCM16 指令，因为新固件专门保留了原始 AUDIO 协议。

## 6. 还有哪些限制

- **12 秒上限仍在**：单条音频最多 12 秒。特别长的回复需要分为两条，段间仍可能有停顿，只是次数和传输等待应明显减少。
- **不是边合成边播放的真正流式音频**：当前必须先把一条音频上传到 PSRAM，再播放，所以首次开口仍有等待。后续若需要达到实时对话体验，才考虑环形缓冲、双缓冲和实际播放状态同步。
- **当前 main.py 是同步播报**：语音播放时主循环不会接受新键盘输入，也不支持“博士说话时立即打断栈酱”。那属于后续麦克风/交互调度里程碑。
- **M5Stack-Avatar 动画保持不变**：音频解压成 16-bit PCM 后仍用于嘴型幅度估算，头部舵机没有参与。
- **未在用户实体硬件上编译、烧录与试听**。脚本做了静态语法检查，用户需按上述操作手动验收，不会生成自动测试文件。
