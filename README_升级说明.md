# Friston-2 x StackChan v0.2：经典动画表情 + Agent 事件适配层

## 这次改了什么

- 屏幕不再显示 `FRISTON-2`、`IDLE`、`THINKING` 等调试文字。使用成熟的 `M5Stack-Avatar` 库作为整屏动态表情：自动眨眼、视线游移、呼吸动画。
- `FACE SPEAKING` 时嘴巴自动开合（**暂时只是程序模拟，不是与声音的真实同步**）。
- 原来的 `PING`、`INFO`、`FACE IDLE`、`FACE THINKING`、`FACE SPEAKING` 保持兼容。
- 扩展：`FACE HAPPY`、`FACE NEUTRAL`、`FACE DOUBT`、`FACE SAD`、`FACE ANGRY`、`FACE SLEEPY`。
- Python 加入非阻塞 `StackChanFaceEvents`，可以在现有 Agent 执行流程中接入状态回调；不会自动修改 `main.py`。
- ESP32 端仍不操作舵机、扬声器、麦克风、摄像头或 Wi-Fi。

> 注意：M5Stack-Avatar 是经典开源动画表情，不是官方出厂固件整套动画资源的逐像素复刻。

## 0. 安全须知

- 确认 v0.1 已经能正常烧录与 COM10 通信，你已备份原厂固件。
- 关闭 Arduino Serial Monitor、miniterm、Python 串口程序，否则烧录端口占用。
- 升级固件会覆盖你刚安装的 v0.1（不是 M5Burner 原厂固件）。保存 v0.1 文件以便回退。
- 新库出现兼容/编译错误时不要修改固件底层烧写配置，先收集完整报错。
- `M5GFX` 对 LCD MODULE E 需要至少 0.2.27；`M5Unified` 和 `M5GFX` 尽量使用成对兼容的版本。已能编译 v0.1 时不必盲目重装。

## 1. Arduino IDE 安装额外库

菜单：工具/项目 -> 管理库（Library Manager），搜索 `M5Stack-Avatar`，安装 **M5Stack-Avatar 0.10.0**。库的头文件为 `Avatar.h`。它依赖已有的 M5Unified/M5GFX。

GitHub：https://github.com/stack-chan/m5stack-avatar （MIT License）

确认工具菜单：开发板 **M5CoreS3**，端口确认 USB ESP32-S3 对应的实际 COM，**USB CDC On Boot = Enabled**；保持你已验证成功的 v0.1 其他设置不变。

## 2. 解压至项目根目录（先备份旧文件）

```powershell
conda activate photorobot
cd C:\Users\FIRST\Desktop\Friston-2
Copy-Item .\stackchan_integration\stackchan_bridge.py .\stackchan_integration\stackchan_bridge.v01.py.bak
Copy-Item .\stackchan_integration\firmware\friston2_stackchan\friston2_stackchan.ino .\stackchan_integration\firmware\friston2_stackchan\friston2_stackchan.v01.ino.bak
Expand-Archive -LiteralPath "$HOME\Downloads\Friston2_StackChan_v02_Avatar.zip" -DestinationPath . -Force
```

这里只覆盖 `stackchan_integration` 内的对应模块，不改 `main.py`、`friston2/` 中 Agent/RAG 以及 SO-101 代码。

## 3. Arduino IDE 烧录新版经典脸

1. 打开 `C:\Users\FIRST\Desktop\Friston-2\stackchan_integration\firmware\friston2_stackchan\friston2_stackchan.ino`。
2. 按 `✓` 编译。若提示 `Avatar.h: No such file or directory`，说明尚未安装 M5Stack-Avatar 库。
3. 确认 v0.1 的显示/通信已验收且有恢复备份后，再上传。
4. 成功后显示经典黑底白脸动画，屏幕上**没有任何文字标签**，闲置时会随机眨眼和视线游移。

## 4. 手动验证（不生成任何自动测试文件）

```powershell
cd C:\Users\FIRST\Desktop\Friston-2
conda activate photorobot
python .\stackchan_integration\stackchan_bridge.py --port COM10
python .\stackchan_integration\stackchan_bridge.py --port COM10 --face thinking
python .\stackchan_integration\stackchan_bridge.py --port COM10 --face speaking
python .\stackchan_integration\stackchan_bridge.py --port COM10 --face happy
python .\stackchan_integration\stackchan_bridge.py --port COM10 --face idle
```

预期第一条输出包含 `FRISTON2_STACKCHAN_V2_AVATAR`，之后按指令改变表情；`speaking` 时嘴巴持续动。COM 变化就按新的实际端口替换。`speaking` 只是嘴巴动画，当前没有播放声音。

## 5. 与 Friston-2 Agent 联动（接口已备好，需对照最新 main.py 安放）

`face_events.py` 提供后台串口发送，不阻塞 Qwen3/Agentic RAG；连接失败时仅记录告警、不会中断原来的项目。

在你已有的 Agent 调用点，使用以下模式（**示意代码，不要不分上下文直接覆盖 main.py**）：

```python
from stackchan_integration import StackChanFaceEvents

face = StackChanFaceEvents(port="COM10")
try:
    # 用户输入后，Agent 推理前：
    face.on_request()
    # existing_result = agent.handle(user_text)
    # 在你真正已经生成或显示回复时：
    face.on_reply_ready()
    # 如果未来有 TTS：播放开始/结束时使用
    # face.on_speech_start()
    # play_audio(...)
    # face.on_speech_end()
finally:
    face.close()
```

上面 `agent.handle` 仅是注释性占位名；**当前真实的 `main.py`/Agent 分发接口未提供，因此本包没有猜测或更改其函数名**。推荐给助手上传最新的 `main.py` 与调用链文件，后续按实际代码插入事件，不引入额外一轮模型调用，也不改变原检索路由。

可选环境变量：

```powershell
$env:FRISTON_STACKCHAN_ENABLED = "1"
$env:FRISTON_STACKCHAN_PORT = "COM10"
```

关闭 StackChan 联动：`$env:FRISTON_STACKCHAN_ENABLED = "0"`。

## 6. 回退与排错

- 只想退回 v0.1：将 `.v01.ino.bak` 复制为 `.ino`、`.v01.py.bak` 复制为 `.py`，再在 Arduino IDE 上传旧 `.ino`。不要简单更改扩展名让备份文件变成被 IDE 额外编译的源码。
- 需要原厂 AI 界面：按之前准备的 M5Burner 官方固件恢复步骤操作（可能需要重新配对）。
- 编译提示 M5Unified 中缺少 `board_M5StackChan`：M5Unified 与 M5GFX 版本不匹配，尝试一并升级到相互兼容的稳定版本。例如 M5Unified 0.2.22 + M5GFX 0.2.29 或更高的兼容组合。
- 新程序重启、黑屏或口型异常：先提供编译日志和屏幕照片，不要持续强行刷机。
- 设备拔线：已接入的后台事件层会记录警告但不停止 Agent，恢复连线后会在下次新的状态事件到来时尝试重连。

## 官方/开源资料

- 官方硬件和 E 屏版本：https://docs.m5stack.com/en/StackChan
- 官方显示 API：https://docs.m5stack.com/en/arduino/stackchan/display
- 开源 M5Stack-Avatar：https://github.com/stack-chan/m5stack-avatar
- M5Unified：https://github.com/m5stack/M5Unified
