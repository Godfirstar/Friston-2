"""Friston-3 exhibit entry point and local owner controls."""
import argparse
import sqlite3

from friston3.npc.identity import IDENTITY


def _arguments():
    parser = argparse.ArgumentParser(description="Friston-3 漫展终端")
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument("--download-owner-models", choices=("face", "voice", "all"),
                           help="显式联网下载身份模型；不启动硬件或对话")
    operation.add_argument("--owner-status", action="store_true",
                           help="查看本地身份模板和模型状态；不启动硬件")
    operation.add_argument("--forget-owner", action="store_true",
                           help="删除主人人脸与声纹模板")
    operation.add_argument("--enroll-owner", choices=("face", "voice", "both"),
                           help="在本地控制台录入主人；不加载 ASR、对话或 TTS")
    return parser.parse_args()


def main():
    arguments = _arguments()
    face = camera = speech_input = owner = None
    try:
        if not any(vars(arguments).values()):
            from friston3.runtime_bootstrap import use_installed_runtime
            child_status = use_installed_runtime()
            if child_status is not None:
                return child_status
        # Setup/status paths return before creating device workers or chat models.
        if arguments.download_owner_models:
            from friston3.owner_setup import download_owner_models
            prepared = download_owner_models(arguments.download_owner_models)
            return 0 if prepared["success"] else 1

        from friston3.owner_identity import OwnerIdentityManager, OwnerProfile

        if arguments.forget_owner:
            OwnerProfile().forget()
            print("主人人脸与声纹模板已删除；对话记忆保持原状。")
            return 0
        if arguments.owner_status:
            owner = OwnerIdentityManager()
            print(owner.status_text())
            return 0

        from friston3.hardware.camera import create_camera
        from stackchan_integration import StackChanFaceEvents

        # Camera, microphone, expressions and playback share one USB/TCP owner.
        face = StackChanFaceEvents()
        camera = create_camera(face)
        owner = OwnerIdentityManager(camera=camera, device=face)
        if arguments.enroll_owner:
            return 0 if owner.enroll(arguments.enroll_owner) else 1

        print(f"{IDENTITY.name} 启动（栈酱摄像头 / 栈酱麦克风 / 自动连接）")
        print("[StackChan] 自动发现 USB 或本地 Wi-Fi；设备尚未连接时会继续等待。")
        owner.warmup()
        from stackchan_integration.voice_input import StackChanSpeechInput
        speech_input = StackChanSpeechInput(face)
        speech_input.audio_observer = owner.begin_utterance
        speech_input.preload()
        _run_session(face, camera, speech_input, owner)
        return 0
    except (EOFError, KeyboardInterrupt):
        print(f"\n{IDENTITY.name} 已退出。")
        return 0
    except Exception as error:
        # Include dependency/configuration failures in the visible startup
        # boundary. No device operation or model download is retried silently.
        print(f"[{IDENTITY.name}] 启动或运行未完成：{type(error).__name__}: {error}")
        print("请检查上面指明的本地模型或依赖；普通设备断线会自动恢复。")
        return 1
    finally:
        for resource in (owner, speech_input, camera, face):
            if resource is not None:
                try:
                    resource.close()
                except Exception as error:
                    print(f"[{IDENTITY.name}] 资源释放未确认：{type(error).__name__}: {error}")


def _discard_input(owner, speech_input):
    owner.invalidate()
    if speech_input is not None:
        speech_input.clear_last_audio()


def _read_instruction(owner, speech_input):
    if speech_input is None:
        return input("\n你: ").strip(), "text"
    try:
        return speech_input.read().strip(), "speech"
    except KeyboardInterrupt:
        # The existing scheduler cancels the active microphone job. Interrupted
        # audio must not identify this local keyboard turn.
        _discard_input(owner, speech_input)
        print("\n已进入电脑控制台。输入命令或一句话；直接按 Enter 返回麦克风。")
        return input("电脑控制台: ").strip(), "text"


def _new_visitor(owner, speech_input, npc):
    _discard_input(owner, speech_input)
    # Explicit handover also ends any pending owner-only demonstration, so it
    # cannot accidentally apply to the next visitor's first utterance.
    profile = owner.handover_visitor()
    # Switching away from owner retains the owner's private state in NPC; it
    # only discards the previous anonymous visitor's temporary conversation.
    npc.set_interlocutor({
        "role": "visitor", "profile_id": profile, "source": "visitor_handover",
        "authenticated": False, "local_operator": False,
        "reason": "现场明确开始接待下一位来访者",
    })
    print(f"{IDENTITY.name}: 已开始新访客会话。")


def _local_control(command, owner, speech_input, npc):
    """Called only for keyboard input. Never expose these as planner tools."""
    words = command.split()
    if not words or words[0] not in ("/owner", "/visitor"):
        return False
    _discard_input(owner, speech_input)
    if words == ["/owner", "status"]:
        print(owner.status_text())
    elif words[:2] == ["/owner", "enroll"] and len(words) in (2, 3):
        owner.enroll(words[2] if len(words) == 3 else "both")
    elif words[:2] == ["/owner", "mode"] and len(words) == 3:
        owner.set_mode(words[2])
        if words[2] == "owner":
            print("已启用主人演示模式，120 秒后自动恢复识别；可用 /owner mode auto 提前结束。")
        elif words[2] == "once":
            print("30 秒内下一条对话按主人处理，随后恢复自动识别。")
        elif words[2] == "visitor":
            _new_visitor(owner, speech_input, npc)
            print("已固定为访客接待模式；用 /owner mode auto 恢复自动识别。")
        else:
            print("已恢复自动识别；电脑键盘输入按主人处理。")
    elif words == ["/owner", "forget"]:
        owner.profile.forget()
        owner.set_mode("auto")
        print("主人人脸与声纹模板已删除；对话记忆保持原状。")
    elif words == ["/visitor", "new"]:
        _new_visitor(owner, speech_input, npc)
    else:
        print("身份命令：/owner status | /owner enroll face|voice|both | "
              "/owner mode auto|owner|visitor|once | /owner forget | /visitor new")
    return True


def _run_session(face, camera, speech_input, owner):
    from friston3.agent import Agent
    from friston3.skills.auto_photo import AutoPhotoSkill
    from friston3.skills.photo import PhotoSkill
    from friston3.skills.manager import SkillManager
    from friston3.skills.npc import NpcTalkSkill
    from friston3.skills.volume import VolumeControlSkill
    from friston3.skills.device_settings import DeviceSettingsSkill
    from friston3.vision.yunet_observer import YuNetObserver
    from friston3.planner import LLMPlanner
    from friston3.input_text import prepare_input_text, is_memory_command
    from stackchan_integration.voice_output import AgentSpeechOutput

    # Warm the resident TTS engine while the dialogue models initialize, and
    # give the volume skill the very same output object used for all replies.
    voice = AgentSpeechOutput(face)
    try:
        manager = SkillManager()
        manager.register(PhotoSkill(camera))
        manager.register(AutoPhotoSkill(camera, observer=YuNetObserver()))
        volume_control = VolumeControlSkill(voice)
        manager.register(volume_control)
        device_settings = DeviceSettingsSkill(face)
        manager.register(device_settings)
        npc = NpcTalkSkill()
        manager.register(npc)
        agent = Agent(manager, LLMPlanner(manager))

        print("可用指令：")
        print("  帮我拍张照 / 自动拍照 / 自动拍照，倒计时5秒")
        print("  声音大一点 / 小一点 / 音量调到百分之五十 / 静音 / 恢复声音 / 现在音量多少")
        print("  自动选择连接 / 切换到 Wi-Fi / 切换到 USB / 现在怎么连接的")
        print("  改成自动收音 / 改成触屏收音 / 关闭表演动作 / 现在放在桌上")
        print("  跟我打个招呼 / 点两下头 / 得意一下 / 看看左边 / 看看主人")
        print("  主人在你左边 / 主人在你右边：按机器人自身视角设置主人方向")
        print("  下一位来访者：结束当前访客临时会话，开始下一位")
        print("  清空记忆：清空当前互动对象的短期记忆")
        print("  记住：内容 / 忘记：关键词 / 清空长期记忆：在主人电脑控制台管理")
        print("  查看证据：查看当前会话上一轮的身份与回复依据")
        print("  /owner status：查看身份状态；/owner enroll face|voice|both：录入主人")
        print("  /owner mode auto|owner|visitor|once：自动、主人演示、访客、下一句主人")
        print("  /owner forget：删除身份模板；/visitor new：开始新访客会话")
        print("  /voice off | on | status | volume 0..100：电脑控制台播音控制（百分比）")
        print("  /device status | auto | usb | wifi：连接状态和连接偏好")
        print("  退出：在电脑控制台或由已确认的主人结束程序")
        if speech_input is not None:
            print("  等待说话时按 Ctrl+C 进入电脑控制台；控制台空行返回麦克风。")

        while True:
            try:
                raw_instruction, input_source = _read_instruction(owner, speech_input)
            except (EOFError, KeyboardInterrupt):
                print(f"\n{IDENTITY.name} 已退出。")
                break
            if not raw_instruction:
                _discard_input(owner, speech_input)
                continue
            # /t is an optional local spelling for one typed utterance.
            if input_source == "text" and raw_instruction.lower().startswith("/t "):
                raw_instruction = raw_instruction[3:].strip()
            instruction = prepare_input_text(
                raw_instruction, input_source=input_source)["normalized_text"]
            command = instruction.rstrip("。！？!?.,，；; ").lower()
            if not instruction:
                _discard_input(owner, speech_input)
                continue

            if input_source == "text":
                if command.split()[:1] == ["/voice"]:
                    _discard_input(owner, speech_input)
                    parts = command.split()
                    try:
                        if len(parts) == 2 and parts[1] in ("off", "on"):
                            volume_control.execute("unmute" if parts[1] == "on" else "mute")
                            print("[StackChanTTS] " + volume_control.status_text())
                        elif len(parts) == 2 and parts[1] == "status":
                            print("[StackChanTTS] " + volume_control.status_text())
                        elif len(parts) == 3 and parts[1] == "volume":
                            volume_control.execute("set", int(parts[2]))
                            print("[StackChanTTS] " + volume_control.status_text())
                        else:
                            print("用法：/voice off | /voice on | /voice status | /voice volume 0..100")
                    except (OSError, ValueError, RuntimeError) as error:
                        print(f"[StackChanTTS] 设置未完成：{error}")
                    continue
                if command.split()[:1] == ["/device"]:
                    _discard_input(owner, speech_input)
                    parts = command.split()
                    try:
                        if len(parts) == 2 and parts[1] in ("auto", "usb", "wifi"):
                            result = device_settings.execute("connection", parts[1])
                            print(f"[StackChan] {result['message']}")
                        elif parts == ["/device", "status"]:
                            print("[StackChan] " + device_settings.status_text())
                            print(face.connection_status())
                        else:
                            print("用法：/device status | /device auto | /device usb | /device wifi")
                    except (OSError, ValueError, RuntimeError) as error:
                        print(f"[StackChan] 设置未完成：{error}")
                    continue
                # The physical console can inspect the actual previous result
                # without replacing a visitor's trace with a new owner turn.
                if command in ("查看证据", "/evidence"):
                    _discard_input(owner, speech_input)
                    print(npc.explain_last_response())
                    continue
                try:
                    if _local_control(command, owner, speech_input, npc):
                        continue
                except (EOFError, KeyboardInterrupt):
                    _discard_input(owner, speech_input)
                    print("电脑控制台操作已取消。")
                    continue
                except Exception as error:
                    _discard_input(owner, speech_input)
                    print(f"身份命令未完成：{type(error).__name__}: {error}")
                    continue
            elif command.split()[:1] in (["/owner"], ["/visitor"]):
                _discard_input(owner, speech_input)
                print(f"{IDENTITY.name}: 这项管理操作需要在电脑控制台输入。")
                continue

            if command in ("下一位来访者", "下一位访客", "新访客"):
                _new_visitor(owner, speech_input, npc)
                continue

            pcm = speech_input.last_audio_pcm if input_source == "speech" else None
            sample_rate = speech_input.last_audio_rate if input_source == "speech" else 16000
            try:
                context = owner.context_for(input_source=input_source, pcm=pcm, sample_rate=sample_rate)
            finally:
                if speech_input is not None:
                    speech_input.clear_last_audio()
            npc.set_interlocutor(context)
            label = "主人 / 可露希尔" if context["role"] == "owner" else (
                "来访者" if context["role"] == "visitor" else "身份未确认")
            print(f"[身份] {label}；依据={context['source']}；额外等待={context['wait_ms']:.0f}ms")

            # Spoken management sees this utterance's context, never the prior
            # speaker's private history or authorization.
            if command in ("退出", "exit", "quit"):
                if input_source == "text" or context["role"] == "owner":
                    break
                print(f"{IDENTITY.name}: 我会继续接待。结束运行需要由主人操作。")
                continue
            if command in ("查看证据", "/evidence"):
                print(npc.explain_last_response())
                continue
            if command in ("清空记忆", "/clear"):
                npc.clear_history()
                print(f"{IDENTITY.name}: 当前会话的短期记忆已清空。")
                continue
            if command == "清空长期记忆":
                try:
                    npc.clear_all_memory()
                except (OSError, sqlite3.Error, RuntimeError) as error:
                    print(f"{IDENTITY.name}: 长期记忆未能清空：{error}")
                else:
                    print(f"{IDENTITY.name}: 已清空保存的对话、长期记录和设备执行记录。")
                continue

            face.on_request()
            try:
                result = agent.handle(raw_instruction, input_source=input_source, interlocutor=context)
                executed = result.get("executed_skill")
                if executed in ("take_photo", "auto_photo"):
                    npc.record_execution(executed, result)
                reply_text = str(result.get("message", "任务结束"))
                if (executed in ("take_photo", "auto_photo")
                        and result.get("success") is True and context["role"] == "owner"):
                    # Presentation only: retain original device result, saved
                    # path, success flag and execution record without changes.
                    reply_text = "可露希尔，" + reply_text
                print(f"{IDENTITY.name}:", reply_text)
                face.prepare_reply(result)
                if (result.get("success", True)
                        and ((executed == "npc_talk" and not is_memory_command(instruction))
                             or (executed in ("control_volume", "configure_device")
                                 and result.get("speak_confirmation")))):
                    voice.speak(reply_text)
                # Speech consumes the gesture just before the first clip.
                # A muted/unavailable voice still gets one bounded gesture.
                face.on_task_completed(result)
            finally:
                face.on_reply_ready()
    finally:
        voice.close()
        _discard_input(owner, speech_input)


if __name__ == "__main__":
    raise SystemExit(main())
