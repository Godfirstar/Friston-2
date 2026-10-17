class Agent:
    """根据用户指令选择已注册的技能。"""

    def __init__(self, skill_manager):
        self.skill_manager = skill_manager

    def handle(self, instruction):
        instruction = instruction.strip()
        print(f"[Agent] 收到命令: {instruction}")

        # 先匹配自动拍照，避免被普通拍照关键词提前截获。
        auto_photo_keywords = ("自动拍照", "自动摄影")
        if any(keyword in instruction for keyword in auto_photo_keywords):
            print("[Agent] 识别意图: auto_photo")
            return self.skill_manager.execute("auto_photo")

        photo_keywords = ("拍照", "拍张照", "拍一张", "合影", "自拍", "照相")
        if any(keyword in instruction for keyword in photo_keywords):
            print("[Agent] 识别意图: take_photo")
            return self.skill_manager.execute("take_photo")

        return {
            "success": False,
            "message": "暂时无法理解这条指令",
        }
