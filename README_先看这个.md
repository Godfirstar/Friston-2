# Friston-2 × StackChan v0.3 — 表情一闪而过排查 + 正式 Agent 联动

**不用重新刷入 Arduino 固件。** 本补丁基于你上传的 `docs(6).zip` 中 `main.py`、`stackchan_bridge.py`、`face_events.py`，不改 `friston2/` 子项目的 Planner/RAG/摄影代码，也不改机械臂。

## 1. 原因判断

原有 `stackchan_bridge.py --face thinking` 每次运行都会打开 COM10、发送命令、然后立刻关闭。M5Stack-Avatar 的 `setExpression()` 不会自行定时恢复到待机，但 ESP32-S3 的串口打开/关闭若触发复位，固件就会从 `setup()` 恢复到默认 `IDLE`，看起来像表情闪一下就消失。

**目前只是高优先级假设，不是已证实的硬件复位。** 第一件事就是保留连接观察。

## 2. 安装（先退出 Arduino 串口监视器、miniterm、以及正在运行的 `python main.py`）

```powershell
cd C:\Users\FIRST\Desktop\Friston-2
conda activate photorobot
Copy-Item .\main.py .\main.pre_stackchan_v03.py.bak
Copy-Item .\stackchan_integration\stackchan_bridge.py .\stackchan_integration\stackchan_bridge.pre_v03.py.bak
Copy-Item .\stackchan_integration\face_events.py .\stackchan_integration\face_events.pre_v03.py.bak
Expand-Archive -LiteralPath "$HOME\Downloads\Friston2_StackChan_v03.zip" -DestinationPath . -Force
```

保留备份用于恢复。ZIP 只覆盖 `main.py` 和 `stackchan_integration` 内的三个 Python 文件，不包含固件/Flash 文件。解压前建议确认电脑上的 main.py 与上传的 docs(6).zip 是同一版，若这段时间你又修改过 main.py，请先保留修改。

## 3. 保持串口打开的手动诊断

```powershell
python .\stackchan_integration\stackchan_bridge.py --port COM10 --face thinking --hold 15
```

观察 **整个 15 秒**：

- 如果思考表情这次持续显示、程序退出后才回到待机 → 高度支持是串口打开/关闭时的复位造成的。
- 如果连接保持时也瞬间回到待机 → 下一步要查 MCU 复位日志、Arduino 程序执行和 Avatar 渲染，先不要反复刷机。
- 如果只是眼睛眨一下，但眉毛、嘴巴一直是思考表情 → 正常的自动眨眼。

也可让串口持续打开，手动输入多种表情：

```powershell
python .\stackchan_integration\stackchan_bridge.py --port COM10 --interactive
```

在 `face>` 提示符后逐个输入 `thinking`、`speaking`、`idle`，等待观察后再输入下一个；`quit` 结束，期间不关闭串口。

## 4. Friston-2 的正式联动

诊断确认后，先关闭上述 CLI（COM10 同一时间只能给一个程序占用），启动项目：

```powershell
$env:FRISTON_STACKCHAN_PORT = "COM10"
$env:FRISTON_STACKCHAN_ENABLED = "1"
python main.py
```

- `main()` 运行期间，后台线程独占并保持 COM10 连接。
- 用户输入实际请求 → `face.on_request()` 切到 `thinking`。
- `Agent.handle()` 或 `SkillManager.execute()` 结束并输出结果 → `face.on_reply_ready()` 切到 `idle`。
- 当前 Friston-2 只向终端打印文字，尚无真正的 TTS，所以 **不会自动进入 speaking**；以后加音频播放时用 `face.on_speech_start()` / `face.on_speech_end()`。
- USB 拔掉或接口异常 → 串口线程按间隔重试，Agent 逻辑仍可运行。

无设备的情况下关闭联动：

```powershell
$env:FRISTON_STACKCHAN_ENABLED = "0"
python main.py
```

### 注意

目前只改软件联动，没有修改 Arduino 的串口控制信号行为。如果 `--hold 15` 期间仍闪退，需要根据实际运行日志再改 ESP32 固件或 USB 控制线。不要把修改 `dtr/rts` 当作已确认的万能修复，ESP32-S3 原生 USB Serial/JTAG 与普通 USB-UART 芯片不完全一样。

## 5. 手动验收标准

1. `--hold 15` 期间表情是否保持 15 秒，退出后是否改变。
2. `--interactive` 模式中连续切换 3 次是否正常。
3. 启动 `main.py` 后，提出一个需要 Ollama 推理的问题，栈酱是否在等待时处于思考表情、结果出来后回待机。
4. 在 `main.py` 运行时不要另外打开 Arduino 串口监视器。

不生成自动测试文件，不自动运行硬件验证程序。
