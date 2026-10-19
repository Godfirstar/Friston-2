import sqlite3

from friston2.agent import Agent
from friston2.skills.auto_photo import AutoPhotoSkill
from friston2.hardware.camera import RealCamera
from friston2.skills.photo import PhotoSkill
from friston2.skills.manager import SkillManager
from friston2.skills.npc import NpcTalkSkill
from friston2.vision.yunet_observer import YuNetObserver
from friston2.planner import LLMPlanner
from friston2.config import CAMERA_INDEX
from friston2.npc.identity import IDENTITY



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

        if instruction.startswith(("记住：", "记住:", "忘记：", "忘记:")):
            # Explicit memory commands do not need an LLM routing call.
            result = manager.execute("npc_talk", {"user_text": instruction})
        else:
            result = agent.handle(instruction)
        if result.get("executed_skill") in ("take_photo", "auto_photo"):
            npc.record_execution(result["executed_skill"], result)
        print(f"{IDENTITY.name}:", result.get("message", "任务结束"))


if __name__ == "__main__":
    main()
