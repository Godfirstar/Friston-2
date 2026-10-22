# Friston-2 × StackChan v0.4 — 离线中文男声 + 栈酱扬声器 + 跟随振幅口型

这次只做一个里程碑：**电脑合成一句中文语音，传到 StackChan K151 内置扬声器播放，动画脸的嘴巴随音频振幅开合**。

- 继续支持 v0.3 的 `PING`、`INFO`、`FACE ...` 指令；v0.3 `main.py`、`face_events.py` 不会被覆盖。
- 不使用云端 TTS，不需要展会现场网络。首次需下载一个中文男声音色模型。
- 不开发自动测试文件、不开启摄像头、麦克风或两个舵机。
- **重要：一定先关闭正在运行的 `python main.py`、Arduino 串口监视器和 miniterm。COM10 同一时间只能被一个进程占用。**

## 一、先备份 v0.3 再解压

在 Windows PowerShell 中：

```powershell
conda activate photorobot
cd C:\Users\FIRST\Desktop\Friston-2
Copy-Item .\stackchan_integration\firmware\friston2_stackchan\friston2_stackchan.ino .\stackchan_integration\firmware\friston2_stackchan\friston2_stackchan.v03.ino.bak
Copy-Item .\stackchan_integration\stackchan_bridge.py .\stackchan_integration\stackchan_bridge.v03.py.bak
Expand-Archive -LiteralPath "$HOME\Downloads\Friston2_StackChan_v04_audio.zip" -DestinationPath . -Force
```

ZIP 只新增/替换这些实际文件：

- `stackchan_integration/firmware/friston2_stackchan/friston2_stackchan.ino` — 屏幕、串口、16k mono PCM16、CRC32、PSRAM 音频缓存、播放振幅嘴巴；
- `stackchan_integration/stackchan_bridge.py` — 旧接口不变，新增 `play_pcm()` 与 `set_volume()`；
- `stackchan_integration/local_tts.py` — 可选离线中文男声 Sherpa-ONNX VITS；
- `stackchan_integration/speak.py` — 命令行 `--beep`、`--wav`、`--text` 手动使用。

**没有 `main.py`，也没有自动测试文件。**

## 二、编译并上传 Arduino v0.4 固件

仍然使用你已跑通的 Arduino IDE 环境，打开：

`C:\Users\FIRST\Desktop\Friston-2\stackchan_integration\firmware\friston2_stackchan\friston2_stackchan.ino`

- Board：`M5CoreS3`；`USB CDC On Boot = Enabled`；端口之前是 COM10，以当前识别的 ESP32-S3 USB 端口为准。
- 原有 `M5Unified`、`M5GFX`、`M5Stack-Avatar` 继续保留；E 屏的 M5GFX >= `0.2.27`，不要装旧版本。
- 确保工具菜单中的 PSRAM 沿用之前成功的 CoreS3 默认启用配置；StackChan K151 硬件有 8MB PSRAM。
- **先点 ✓ 验证编译，成功后点 → 上传。** 不要设置擦除全部 Flash。
- 固件刷完后，屏幕仍应是 v0.3 经典动态表情，没添加任何丑标签。

音频协议：

`AUDIO <字节数> <8位CRC32>\n` → MCU 回复 `READY AUDIO` → 电脑传输**不带换行**的二进制 16kHz/单声道/16-bit PCM → MCU 回复 `AUDIO STARTED` → 扬声器播放+嘴巴开合 → MCU 回复 `AUDIO DONE`。

每条音频 **上限 12 秒（384000 字节）**，在 PSRAM 中完整缓存后播放，音频上传完成前不会开口。音量默认 80/255，建议从低音量开始。

## 三、先用 1 秒提示音验证扬声器（不下载模型）

**关闭 `python main.py`**，回到 photorobot 环境：

```powershell
cd C:\Users\FIRST\Desktop\Friston-2
conda activate photorobot
python -m stackchan_integration.speak --port COM10 --beep --volume 60
```

你应该从**栈酱自带扬声器**听到连续三个短音调，屏幕嘴巴跟随变化，命令行最后显示 `AUDIO DONE`。

> 没听到但显示 DONE：先靠近栈酱确认是否是电脑发声（这里没有调用电脑的音频播放 API），然后检查 Speaker 初始化、音量、设备内置功放。

## 四、安装离线中文男声（单独 Conda 环境，保护 photorobot）

建议使用 `friston_tts` 环境，不要往现在已装有 LeRobot、Ollama 接口、相机组件的 `photorobot` 环境继续堆 TTS 依赖。

```powershell
conda create -n friston_tts python=3.11 -y
conda activate friston_tts
python -m pip install sherpa-onnx numpy pyserial
```

下载 sherpa-onnx 官方提供的中文男声模型（需要网络，**只下载一次**）：

```powershell
$modelRoot = Join-Path $env:LOCALAPPDATA 'Friston2\tts'
New-Item -ItemType Directory -Force -Path $modelRoot | Out-Null
$url = 'https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/vits-zh-hf-fanchen-wnj.tar.bz2'
$archive = Join-Path $modelRoot 'vits-zh-hf-fanchen-wnj.tar.bz2'
curl.exe -L --fail --retry 3 -o "$archive" "$url"
tar.exe -xjf "$archive" -C "$modelRoot"
```

核对模型文件：

```powershell
Get-Item "$modelRoot\vits-zh-hf-fanchen-wnj\vits-zh-hf-fanchen-wnj.onnx"
```

模型与声音由 sherpa-onnx 官方预训练音频模型列表提供，音色是普通中文男声，**不代表已经复刻 Friston-2 的机械角色声线**。官网：
https://k2-fsa.github.io/sherpa/onnx/tts/pretrained_models/vits.html

把模型放 `%LOCALAPPDATA%` 是为了避免 ResearchJournal、Git 和 RAG 知识库扫描大文件。

## 五、让它真正说一句中文

确认上面第三步能发出提示音，再运行（一定要保持 `main.py` 关闭）：

```powershell
cd C:\Users\FIRST\Desktop\Friston-2
conda activate friston_tts
python -m stackchan_integration.speak --port COM10 --text "博士，我已经准备好了。" --volume 80
```

预期：命令行显示本地合成完毕 → 传输音频 → 栈酱开口播出这句中文 → 嘴巴随声音起伏 → `AUDIO DONE` → 自动回待机表情。

如果声音太大/太小，可用 `--volume 40` 或 `--volume 110` 调整。初期不要超过 160。

## 六、可能遇到的错误

| 报错 / 现象 | 首选操作 |
|---|---|
| `cannot open COM10` / Access is denied | 关闭 `main.py`、Arduino 串口监视器、miniterm 后再试；端口变化时重新识别 |
| `Upload v0.4 audio firmware` | Arduino 固件仍是 v0.2/v0.3，必须先刷上 v0.4 |
| `ERR AUDIO_PSRAM` | 检查 Arduino 的 PSRAM 是否启用、内存是否够（CoreS3 有 8MB） |
| `ERR AUDIO_CRC` | 传输内容不完整或损坏；重新连接、重试一次，必要时换 USB 线 |
| `ERR SPEAKER_UNAVAILABLE` | 初始化 Speaker 失败，先核对开发板选择与 M5Unified 版本 |
| `AUDIO STARTED` 后无声 | 降低/提高音量，靠近扬声器；如无声但音频 done，提供串口输出/开发板配置 |
| `No module named sherpa_onnx` | 检查是不是进入 `friston_tts` 环境，执行安装命令 |
| `Offline male voice model missing` | 按上面路径检查模型的解压层级，不能把 tar.bz2 原样放到目录里 |
| `longer than 12 seconds` | 把文本缩成一句更短的话；后续版本再加入分句播放 |
| 原有表情不工作 | 先运行 v0.3 的 `--interactive`，检查兼容接口；必要时恢复备份旧 `.ino` 并重新刷写 |

## 七、为什么这次没有修改 main.py？

你现有的 `main.py`（v0.3）调用 `StackChanFaceEvents` 后台线程长时间独占 COM10，能够稳定控制表情。直接让另一个 TTS 进程也打开 COM10，会导致端口冲突。所以这次**先手动验收音频链路**；确认中文语音和音量正常后，再把音频播放工作交给 v0.3 那个**同一条串口工作线程**，正式打通 Agent 的回答事件。

此时的嘴巴是基于 PCM 音频振幅的近似同步，不是逐音素口型。未来可以优化音素级 viseme 或定制声线，但不影响这个版本的里程碑。

## 八、回退与源码资料

- 恢复 v0.3 Arduino：把 `friston2_stackchan.v03.ino.bak` 内容复制回 `friston2_stackchan.ino`，Arduino IDE 再次编译上传（备份文件不要重命名成同目录第二个 `.ino`）。
- 恢复 v0.3 Python：把 `stackchan_bridge.v03.py.bak` 复制回 `stackchan_bridge.py`。
- 完全恢复原厂：使用你以前准备好的 M5Burner 官方固件；**不要随意整片擦除 Flash**。

官方文档：
- StackChan 参数（1W 扬声器、8MB PSRAM）：https://docs.m5stack.com/en/StackChan
- StackChan 官方扬声器接口：https://docs.m5stack.com/en/arduino/stackchan/speaker
- M5Unified 16-bit `playRaw`：https://github.com/m5stack/M5Unified/blob/master/src/utility/Speaker_Class.hpp
- Sherpa-ONNX 模型与下载方法：https://k2-fsa.github.io/sherpa/onnx/tts/pretrained_models/vits.html

**在助手环境仅进行了源代码静态检查，尚未在真实 Arduino/Windows 栈酱上烧写和验收。遇到错误请给出完整第一条编译/串口报错。**
