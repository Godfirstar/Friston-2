# Friston-2 × StackChan v0.4.1：修复 AUDIO_RX_TIMEOUT

这次只改 **USB PCM 传输协议**，不改 TTS 模型、不改屏幕表情、不改 Agent、RAG、SO-101，也不新增自动测试文件。**需要同时更新 Arduino 固件和 Python bridge**；新旧音频协议不能交叉使用。

## 原因与改动

v0.4 的 PC 端在收到 `READY AUDIO` 后，连续以 4096 字节为一块发送 PCM，却不等待 MCU 接收确认。ESP32-S3 的原生 USB CDC 接收缓冲较小，而 MCU 还在逐字节计算 CRC；大段传输时可能丢包，最终停在 `ERR AUDIO_RX_TIMEOUT`。这是高度可疑的原因，不能仅凭超时日志证明具体丢包位置。

v0.4.1 改为 PC 每发 **最多 128 字节** → MCU 收满一块回复 `ACK <累计字节数>` → PC 继续。最后一块确认完毕后，固件才校验 CRC 并播放。接收超时时固件额外输出已收到/目标字节数，方便定位。

## 1. 安装（先备份）

先停止 `python main.py`、串口监视器、miniterm，防止 COM10 冲突。

```powershell
conda activate photorobot
cd C:\Users\FIRST\Desktop\Friston-2
Copy-Item .\stackchan_integration\stackchan_bridge.py .\stackchan_integration\stackchan_bridge.v04.py.bak
Copy-Item .\stackchan_integration\firmware\friston2_stackchan\friston2_stackchan.ino .\stackchan_integration\firmware\friston2_stackchan\friston2_stackchan.v04.ino.bak
Expand-Archive -LiteralPath "$HOME\Downloads\Friston2_StackChan_v041_ack.zip" -DestinationPath . -Force
```

只覆盖两个程序文件，**不会覆盖 main.py、face_events.py、speak.py 或 local_tts.py**。原来的 `friston_tts` 环境及模型保持不变。

## 2. Arduino IDE 编译并上传

打开：

`C:\Users\FIRST\Desktop\Friston-2\stackchan_integration\firmware\friston2_stackchan\friston2_stackchan.ino`

- 开发板继续选择 `M5CoreS3`，原有 COM10 或当前枚举出的端口。
- 保持 `USB CDC On Boot = Enabled`、已验证兼容的 M5GFX（≥0.2.27）和 M5Stack-Avatar 库。
- 保持先前 v0.4 验证通过的其他设置；先点击 **验证 (✓)** 编译，再点击 **上传 (→)**。
- 成功后屏幕仍应显示无文字标签的动态表情，串口 `INFO` 应包含 `FRISTON2_STACKCHAN_V4_AUDIO_PCM16_16K_ACK128`。

请不要同时运行 `main.py` 和 `speak.py`：它们不能同时持有 COM10。

## 3. 手动验收（不用跑自动测试）

建议先用短音调确认传输和扬声器：

```powershell
cd C:\Users\FIRST\Desktop\Friston-2
conda activate photorobot
python -m stackchan_integration.speak --port COM10 --beep --volume 40
```

预期：主机输出 `FRISTON2_STACKCHAN_V4_AUDIO_PCM16_16K_ACK128` → 传输成功 → 栈酱播放三个音调，嘴巴随声音活动 → `AUDIO DONE`。

随后回到独立的 TTS 环境：

```powershell
conda activate friston_tts
python -m stackchan_integration.speak --port COM10 --text "博士，我已经准备好了。" --volume 60
```

预期：音频完成传输后，栈酱自己发声，口型随音频振幅变化，最后返回待机。

## 4. 如果仍报错

- `not running v0.4.1 ACK128`：新 Python bridge 已安装，但 Arduino 固件仍是 v0.4，重新确认固件上传情况。
- `PCM transfer stopped at 128/59770` 或其他字节数：把完整错误发来。该数字是主机等待哪块 ACK 时发生问题；`offset` 是前面已确认的字节数。
- `ERR AUDIO_RX_TIMEOUT received=... expected=...`：记录两数字；不要连续尝试长音频，先检查 USB 线和设备是否异常重启。
- `ERR AUDIO_CRC`：完整长度到了，但内容不同，排查数据破坏。
- `AUDIO STARTED` 后无声：才需要调查扬声器初始化/音量，不要重新装 TTS。
- `COM10 Access denied`：关闭 Arduino 串口监视器、`main.py` 等进程后重试。

## 5. 恢复 v0.4

将 `.v04.ino.bak` 内容恢复到原 `.ino` 并上传旧固件，同时将 `.v04.py.bak` 恢复到 `stackchan_bridge.py`。**固件和 Python 音频协议要成对回退**。不要删除原厂 Flash 备份。

本补丁经过 Python 源码静态语法检查，但未在你的实体 StackChan 上编译或运行音频，需要手动验收。
