
import os
import re

from friston3.npc.dialogue import EXPLANATIONS, bare_text, conversation_act


def _obviously_chat_only(instruction):
    """Optional conservative fast route for clear NPC conversation.

    Only bypasses the planner in favor of npc_talk. Never enables a physical
    action, and does not bypass SkillManager's normal validation.
    """
    if os.getenv('FRISTON_NPC_ROUTE_FAST', '1') != '1':
        return False
    raw = str(instruction or '').strip()
    if not raw or len(raw) > 500:
        return False
    # Ambiguous mentions of hardware (including newly planned arm controls)
    # remain with Planner. False negatives here only cost speed, not safety.
    if re.search(r'拍照|拍张|拍摄|自动拍|照相|合影|来一张|拍一|拍个|拍下|倒计时|倒数|相机|摄像头|镜头|机械臂|机器人手臂|舵机|夹取|抓取|递给|递我|拿给|帮我拿|移动|导航|执行|运行|打开|关闭|启动|停止|photo|camera|picture|capture|move|turn|grab', raw, re.I):
        return False
    if bare_text(raw) in EXPLANATIONS | {'好的', '好', '嗯', '明白了', '哈哈', '原来如此'}:
        return True
    if conversation_act(raw) in ('greeting', 'thanks', 'compliment', 'emotion',
                                'acknowledgement', 'personal_report'):
        return True
    if re.match(r'^(?:(?:请|给我|简单|详细|先|再|能不能|可以)(?:简单|详细)?)*(?:认识|你认识|你知道|你觉得|你认为|你怎么看|如何看待|谈谈|聊聊|我想聊|介绍|讲讲|解释|说说|你最喜欢|你的理想|你去过|你喜欢)', raw):
        return True
    # Short noun/character questions such as "陈是谁" or "天灾是什么".
    if re.fullmatch(r'[^，,。！!；;]{1,30}(?:是谁|是什么|怎么样|有哪些|吗|呢)[？?]?', raw):
        return True
    if re.search(r'为什么|为何|什么|怎么样|如何|谁|哪个|哪位|哪里|多少|是不是|对吧|我(?:指|说)的是|[？?]$', raw):
        return True
    return False


class Agent:
    """通过 Planner 生成技能调用，再交由管理器执行。"""

    def __init__(self, skill_manager, planner):
        self.skill_manager = skill_manager
        self.planner = planner

    def handle(self, instruction):
        print(f"[Agent] 收到指令: {instruction}")

        # 1. Clearly bounded greetings/thanks cannot request an action.
        # Avoid an extra model call just to route them to npc_talk.
        if (instruction.startswith(("记住：", "记住:", "忘记：", "忘记:"))
                or _obviously_chat_only(instruction)):
            call = {'skill': 'npc_talk', 'arguments': {}}
            print('[Agent] 仅绕过设备 LLMPlanner；NPC 内部仍独立判断是否需要事实检索')
        else:
            call = self.planner.plan(instruction)

        if call is None:
            return {
                "success": False,
                "message": "暂不支持这条指令"
            }

        if call["skill"] == "npc_talk":
            call["arguments"] = {
                "user_text": instruction
            }

        print(f"[Agent] 工具调用: {call}")

        # 2. 由 Skill Manager 验证并执行
        return self.skill_manager.execute(
            call["skill"],
            call["arguments"]
        )
