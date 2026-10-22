# Friston-2 × StackChan：第 1 阶段，USB 串口控制表情

目标：本地 Windows Python → COM 串口 → StackChan 屏幕的 IDLE / THINKING / SPEAKING 表情。**不控制舵机，不连接云服务，不修改现有 Agent 或 RAG。**

## 使用前核对

- 设备：M5Stack StackChan K151，新版 LCD MODULE E（ILI9342E）。
- 官方板卡：M5CoreS3；M5Stack Arduino board manager ≥ 3.2.2。
- 库：M5Unified ≥ 0.2.11、**M5GFX ≥ 0.2.27**。
- 工具 → USB CDC On Boot → Enabled（如果存在该选项）。
- Python 环境：`photorobot`，已安装 `pyserial`。
- 请确保已保存**完整且大小正确的原厂 Flash 备份**，M5Burner 已下载对应官方恢复固件。

## 1. 解压

将 ZIP 内容解压至 `C:\Users\FIRST\Desktop\Friston-2`，得到：

```
Friston-2/
  stackchan_integration/
    __init__.py
    stackchan_bridge.py
    firmware/
      friston2_stackchan/
        friston2_stackchan.ino
```

不会覆盖已有 `main.py`、RAG、人物数据、SO101 或机器人控制代码。

## 2. 用 Arduino IDE 编译、刷写

1. 先退出串口监视器（miniterm 按 Ctrl+]），并关闭所有占用该 COM 口的程序，包括 M5Burner。
2. Arduino IDE → 文件 → 打开 → 选择 `stackchan_integration/firmware/friston2_stackchan/friston2_stackchan.ino`。
3. 开发板选 `M5CoreS3`，端口选设备管理器中 ESP32-S3 的 COM（上次是 COM10）。
4. `USB CDC On Boot` 设为 `Enabled`。如果有 USB Mode 选项，优先保持原生硬件 USB Serial/JTAG 对应的默认设置。
5. **先点击验证/编译**，确认无红色错误，然后点击上传。
6. 如无法自动进入下载模式，按官方说明：设备接 USB，按住 RST 约 3 秒直至邻近指示灯变绿，松开进入下载模式，然后重新上传。
7. 上传成功后设备应显示 `FRISTON-2` 和简易笑脸，底部 `IDLE`。某些情况下需要短按 RST 重启或重插 USB 重新枚举 COM。

**提示：上传会取代原厂程序。** 之前的 App 界面暂时不会再出现。未经过用户设备实测前，无法保证所有组合一次编译通过；把 Arduino IDE 的完整错误输出提供给助手即可定位。

## 3. Windows PowerShell 手动验证（不运行自动测试文件）

```powershell
cd C:\Users\FIRST\Desktop\Friston-2
conda activate photorobot
python .\stackchan_integration\stackchan_bridge.py --port COM10
python .\stackchan_integration\stackchan_bridge.py --port COM10 --face thinking
python .\stackchan_integration\stackchan_bridge.py --port COM10 --face speaking
python .\stackchan_integration\stackchan_bridge.py --port COM10 --face idle
```

首次命令预期打印 `Connected: COM10` 以及 `PING -> PONG`。随后屏幕应随三条指令依次切换。

注意：不要同时打开 Arduino 串口监视器、miniterm 与 Python。若 COM 号变化，先运行：

```powershell
python -m serial.tools.list_ports -v
```

## 4. 在 Friston-2 中调用（示例接口，暂不改 main.py）

```python
from stackchan_integration import StackChanBridge

stackchan = StackChanBridge(port="COM10")
try:
    stackchan.connect()
    stackchan.set_face("thinking")
    # 这里执行已有的 Agent / RAG 推理
    # 如果已经在播放 TTS，可以在播放期间设为 "speaking"
finally:
    try:
        stackchan.set_face("idle")
    except Exception:
        pass
    stackchan.close()
```

这是嵌入接口示意。**不要直接照搬并覆盖现有 main.py**，需要对照你最新的执行流程，把设置表情的调用安插到正确位置。

## 5. 出问题如何回退

- 如果仅串口收不到：先检查是否有其他程序占用端口、USB CDC 是否开启、上传后 COM 是否变化；不要马上反复刷机。
- 如果黑屏：核查 M5GFX 是否 ≥ 0.2.27，板卡是否 M5CoreS3。
- 恢复原厂：使用预先下载的官方 StackChan 固件，通过 M5Burner 选择**正确设备与端口**刷回。不需要尝试擦除全片或随意手动写原厂备份。
- 原厂 Flash bin 包含可能的私人凭据与设备绑定信息：不要上传到公开仓库。

## 6. 参考官方资料

- StackChan K151 产品、E 屏变更：https://docs.m5stack.com/en/StackChan
- 官方 StackChan 显示示例：https://docs.m5stack.com/en/arduino/stackchan/display
- 官方 BSP：https://github.com/m5stack/StackChan-BSP

本包仅提供最小功能的开发文件，并没有在你的实体设备上编译或烧录；实物运行结果请按上述步骤人工验收。
