from friston2.agent import Agent
from friston2.skills.auto_photo import AutoPhotoSkill
from friston2.hardware.camera import RealCamera
from friston2.skills.photo import PhotoSkill
from friston2.skills.manager import SkillManager
from friston2.skills.npc import NpcTalkSkill
from friston2.vision.yunet_observer import YuNetObserver
from friston2.planner import LLMPlanner
from friston2.config import CAMERA_INDEX



def main():
    print("Friston-2 启动（真实摄像头模式）")
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
    print("  退出：结束程序")

    while True:
        try:
            instruction = input("\n你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nFriston-2 已退出。")
            break

        if instruction.lower() in ("退出", "exit", "quit"):
            break
        if instruction in ("清空记忆", "/clear"):
            npc.clear_history()

            print("Friston-2: 本次会话的短期记忆已清空。")
            continue
        if not instruction:
            continue

        result = agent.handle(instruction)
        print("Friston-2:", result.get("message", "任务结束"))


if __name__ == "__main__":
    main()
