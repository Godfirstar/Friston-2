
import re


class RulePlanner:
    """临时规则规划器，只接受少数明确指令。"""

    def plan(self, instruction):
        # 去掉空格和常见标点
        text = re.sub(r"[\s，,。！!]", "", instruction.strip())

        # 自动拍照，可选倒计时参数
        match = re.fullmatch(
            r"(?:请|帮我|请帮我)?(?:自动拍照|自动摄影)"
            r"(?:倒计时(?P<seconds>\d+)秒)?",
            text,
        )

        if match:
            arguments = {}

            seconds = match.group("seconds")

            if seconds is not None:
                arguments["countdown_seconds"] = int(seconds)

            return {
                "skill": "auto_photo",
                "arguments": arguments
            }

        # 普通拍照
        if re.fullmatch(
            r"(?:请|帮我|请帮我)?"
            r"(?:拍照|拍张照|拍一张|合影|自拍|照相)",
            text,
        ):
            return {
                "skill": "take_photo",
                "arguments": {}
            }

        return None
