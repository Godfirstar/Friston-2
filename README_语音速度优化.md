# Friston-2 × StackChan v0.5.1 —— 首句更快、段间更连贯

**此次只改 PC 端两个文件，无须烧录 ESP32 固件。** 继续保留已正常工作的 v0.4.1 ACK128 固件、`stackchan_bridge.py`、`face_events.py`、`main.py` 和 sherpa-onnx 男声模型。

## 问题与改动

旧版每次回答都新建 `friston_tts` Python 子进程并加载模型。`tts_export.py` 将全文拆为最长约 38 字的语音段，等**所有段都生成并写盘后**才依次通过串口播放。栈酱固件 ACK128 还要求每 128 字节确认一次，声音的段间延迟因此很明显。

v0.5.1 改为：

- 主程序启动时后台创建 **一个常驻 TTS 子进程**（模型只加载一次）。
- 第一段语音合成完就交给唯一 COM10 线程播放；TTS 子进程同时继续生成后续段。
- 默认 52 字以内优先组成一个片段；若实际语音超过单段 12 秒限制，自动分半。
- 每段打印合成等待时间、PCM 时长、串口发送与播放合并耗时，帮助确认下一个瓶颈。
- 没有引入云服务、没有更改角色/Agent 逻辑，也没有安装任何新依赖。维持文本回答失败降级。

**注意：目前尚非边生成 Qwen token 边说话；且 ACK128 串口发送仍为瓶颈。** 这版优化 TTS 冷启动与串行合成等待，不能完全消除两段音频在串口上传之间的静音。下一步如果大部分耗时确实花在串口，就需要在稳定备份基础上修改 ESP32 协议，考虑更大安全块及双缓冲，不宜盲目直接调大现有 Python 发送长度（会破坏 ACK128 握手）。

## 安装

关闭 `main.py` 和所有占用 COM10 的串口程序，运行：

```powershell
cd C:\Users\FIRST\Desktop\Friston-2
conda activate photorobot

Copy-Item .\stackchan_integration\voice_output.py .\stackchan_integration\voice_output.pre_v051.py.bak

Expand-Archive -LiteralPath "$HOME\Downloads\Friston2_StackChan_v051_fast_tts.zip" -DestinationPath . -Force
```

仅覆盖 `stackchan_integration/voice_output.py`，新增 `stackchan_integration/tts_daemon.py`。不用覆盖 `main.py`。

## 手动验收（不运行自动测试）

```powershell
$env:FRISTON_STACKCHAN_ENABLED = "1"
$env:FRISTON_STACKCHAN_PORT = "COM10"
$env:FRISTON_TTS_AUTO = "1"
$env:FRISTON_TTS_VOLUME = "60"
python main.py
```

先说「你好」，再说「介绍一下自己」。现在终端预期增加：

```text
[StackChanTTS] 后台预热离线语音引擎（只加载一次模型）。
[StackChanTTS] 模型已就绪，加载耗时 ...s
[StackChanTTS] 第 1 段已合成：音频 ...s，距回复打印 ...s；正在传输播放...
[StackChanTTS] 第 1 段完成：传输+播放 ...s，其中音频约 ...s。
...
[StackChanTTS] 完成 N 段：实际音频 ...s，设备传输+播放 ...s，本轮总耗时 ...s。
```

观察：首句文字出来多久听到声音、音频段间是否缩短、每段传输+播放是否显著大于实际音频时长。如果每 3 秒音频都要 8 秒甚至更久才能播完，下一阶段就优先优化 ACK128 固件与 USB 通信。

可选分段长度（25 至 72 字，默认 52）：

```powershell
$env:FRISTON_TTS_SEGMENT_CHARS = "52"
```

对于特别慢或停顿不自然的分段，可以试 45 或 60（建议先使用默认值）。

## 回退

关闭 `main.py`，还原上次保存的旧文件：

```powershell
Copy-Item .\stackchan_integration\voice_output.pre_v051.py.bak .\stackchan_integration\voice_output.py -Force
```

设备固件没有改，无须重刷。

## 手动结果需要反馈的内容

请把 v0.5.1 下「你好」和「介绍一下自己」各一轮新增的 `[StackChanTTS]` 日志发来，特别是“距回复打印”和“传输+播放”。我们据此决定是下一轮优化 ACK 分块还是转向 TTS 提前生成。
