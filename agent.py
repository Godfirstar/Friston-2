
class Agent:
    """通过 Planner 生成技能调用，再交由管理器执行。"""

    def __init__(self, skill_manager, planner):
        self.skill_manager = skill_manager
        self.planner = planner

    def handle(self, instruction):
        print(f"[Agent] 收到指令: {instruction}")

        # 1. 生成结构化工具调用
        call = self.planner.plan(instruction)

        if call is None:
            return {
                "success": False,
                "message": "暂不支持这条指令"
            }

        print(f"[Agent] 工具调用: {call}")

        # 2. 由 Skill Manager 验证并执行
        return self.skill_manager.execute(
            call["skill"],
            call["arguments"]
        )
