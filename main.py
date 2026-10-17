from agent import Agent
from auto_photo import AutoPhotoSkill
from devices import RealCamera
from skills import PhotoSkill, SkillManager, NpcTalkSkill
from yunet_observer import YuNetObserver
from llm_planner import LLMPlanner



CAMERA_INDEX = 0


def main():
    print("PhotoRobot v0.7 启动（真实摄像头模式）")
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
            print("\nPhotoRobot 已退出。")
            break

        if instruction.lower() in ("退出", "exit", "quit"):
            break
        if instruction in ("清空记忆", "/clear"):
            npc.clear_history()

            print("PhotoRobot: 本次会话的短期记忆已清空。")
            continue
        if not instruction:
            continue

        result = agent.handle(instruction)
        print("PhotoRobot:", result.get("message", "任务结束"))


if __name__ == "__main__":
    main()
