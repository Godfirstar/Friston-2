import sqlite3

from friston3.agent import Agent
from friston3.skills.auto_photo import AutoPhotoSkill
from friston3.hardware.camera import RealCamera
from friston3.skills.photo import PhotoSkill
from friston3.skills.manager import SkillManager
from friston3.skills.npc import NpcTalkSkill
from friston3.vision.yunet_observer import YuNetObserver
from friston3.planner import LLMPlanner
from friston3.config import CAMERA_INDEX
from friston3.npc.identity import IDENTITY
from stackchan_integration import StackChanFaceEvents
from stackchan_integration.voice_output import AgentSpeechOutput



def main():
    print(f"{IDENTITY.name} 启动（真实摄像头模式）")
    camera = RealCamera(index=CAMERA_INDEX)
    manager = SkillManager()

    # 普通拍照与自动摄影共用同一个真实摄像头适配器。
    manager.register(PhotoSkill(camera))
    manager.register(
        AutoPhotoSkill(
            camera,
            observer=YuNetObserver()
        )
    )

    npc = NpcTalkSkill()
    manager.register(npc)

    agent = Agent(
        manager,
        LLMPlanner(manager)
    )

    print("可用指令：")
    print("  帮我拍张照：普通拍照")
    print("  自动拍照：实时人脸检测、构图判断与倒计时拍照")
    print("  自动拍照，倒计时5秒：自定义倒计时")
    print("  清空记忆：删除本次会话短期记录")
    print("  记住：内容 / 忘记：关键词：管理你明确告诉我的长期记录")
    print("  清空长期记忆：删除保存的对话、用户记录和设备执行记录")
    print("  查看证据：查看上一轮实际输入的主体、来源和处理路径")
    print("  退出：结束程序")

    # 在 main() 生命周期内只建立一次硬件通信；后台线程独占串口。
    # 可设 FRISTON_STACKCHAN_ENABLED=0 禁用；失联时不阻塞主流程。
    face = StackChanFaceEvents()
    voice = AgentSpeechOutput(face)
    try:
        while True:
            try:
                instruction = input("\n你: ").strip()
            except (EOFError, KeyboardInterrupt):
                print(f"\n{IDENTITY.name} 已退出。")
                break

            if instruction.lower() in ("退出", "exit", "quit"):
                break
            if instruction in ("查看证据", "/evidence"):
                print(npc.explain_last_response())
                continue
            if instruction in ("清空记忆", "/clear"):
                npc.clear_history()

                print(f"{IDENTITY.name}: 本次会话的短期记忆已清空。")
                continue
            if instruction == "清空长期记忆":
                try:
                    npc.clear_all_memory()
                except (OSError, sqlite3.Error, RuntimeError) as error:
                    print(f"{IDENTITY.name}: 长期记忆未能清空：{error}")
                else:
                    print(f"{IDENTITY.name}: 已清空保存的对话、长期记录和设备执行记录。")
                continue
            if not instruction:
                continue

            # 只有真实请求才触发思考表情。连接失败不会阻塞原有 Agent。
            face.on_request()
            try:
                if instruction.startswith(("记住：", "记住:", "忘记：", "忘记:")):
                    # 明确的记忆指令直接交给 NPC，不额外调用 Planner。
                    result = manager.execute("npc_talk", {"user_text": instruction})
                else:
                    result = agent.handle(instruction)
                if result.get("executed_skill") in ("take_photo", "auto_photo"):
                    npc.record_execution(result["executed_skill"], result)
                reply_text = str(result.get("message", "任务结束"))
                print(f"{IDENTITY.name}:", reply_text)

                # Only speak successful NPC chat; photo/arm/management remain text-only.
                # TTS runs offline in the separate friston_tts environment.
                # The pre-existing face worker is the ONLY owner of COM10.
                if (result.get("success", True)
                        and result.get("executed_skill") == "npc_talk"
                        and not instruction.startswith(("记住：", "记住:", "忘记：", "忘记:"))):
                    voice.speak(reply_text)
            finally:
                # The Arduino audio firmware resets its avatar after AUDIO DONE.
                # This also restores IDLE if synthesis/transmission fails.
                face.on_reply_ready()
    finally:
        face.close()


if __name__ == "__main__":
    main()
