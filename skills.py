import json
from urllib.request import Request, urlopen

class PhotoSkill:
    """普通单张拍照技能。"""

    name = "take_photo"
    parameters = {}

    def __init__(self, camera):
        self.camera = camera

    def execute(self):
        print("[PhotoSkill] 开始执行拍照技能")
        try:
            result = self.camera.capture()
        except Exception as error:
            return {
                "success": False,
                "message": f"拍照发生异常：{error}",
            }

        if not result.get("success"):
            return {
                "success": False,
                "message": result.get("error", "拍照失败"),
            }

        path = result["path"]
        return {
            "success": True,
            "path": path,
            "message": f"真实拍照完成！保存至：{path}",
        }

class NpcTalkSkill:
    """通过本地 LLM 生成 NPC 角色回复。"""

    name = "npc_talk"

    parameters = {
        "user_text": {
            "type": str,
            "default": "",
            "min_length": 1,
            "max_length": 500,
        }
    }

    def __init__(
        self,
        model="qwen3.5:2b-q4_K_M",
        url="http://127.0.0.1:11434/api/chat",
    ):
        self.model = model
        self.url = url

    def execute(self, user_text):
        system_prompt = (
            "你是 PhotoRobot，罗德岛工程部外勤终端。"
            "研发负责人是可露希尔。"
            "你对研发经费非常敏感，偶尔想偷懒，"
            "嘴硬、俏皮、略带毒舌，"
            "但工作认真，对来访者有礼貌。"
            "只用中文自然回复，控制在一到三句话，"
            "尽量不超过120字。"
            "不知道真实经费余额时直说，"
            "不能编造真实数字。"
            "你正在聊天，不要声称完成了拍照、"
            "机械臂等实际操作。"
        )

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": user_text
                }
            ],
            "stream": False,
            "think": False,
            "keep_alive": "30s",
            "options": {
                "temperature": 0.7,
                "num_ctx": 2048,
                "num_predict": 160
            }
        }

        try:
            request = Request(
                self.url,
                data=json.dumps(
                    payload,
                    ensure_ascii=False
                ).encode("utf-8"),
                headers={
                    "Content-Type": "application/json"
                },
                method="POST"
            )

            with urlopen(request, timeout=90) as response:
                result = json.load(response)

            reply = result["message"]["content"].strip()

        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            AttributeError
        ) as error:
            return {
                "success": False,
                "message": f"NPC 对话生成失败：{error}"
            }

        if not reply:
            return {
                "success": False,
                "message": "NPC 没有生成有效回复"
            }

        return {
            "success": True,
            "message": reply
        }



class SkillManager:
    """负责技能注册、参数验证和执行。"""

    def __init__(self):
        self.skills = {}

    def register(self, skill):
        self.skills[skill.name] = skill

    def execute(self, name, arguments=None):
        if not isinstance(name, str):
            return {
                "success": False,
                "message": "技能名称必须是字符串"
            }

        skill = self.skills.get(name)

        if skill is None:
            return {
                "success": False,
                "message": f"找不到技能：{name}"
            }

        if arguments is None:
            arguments = {}

        if not isinstance(arguments, dict):
            return {
                "success": False,
                "message": "arguments 必须是字典"
            }

        # 读取该技能允许接收的参数
        specs = skill.parameters

        # 拒绝没有声明的参数
        extra = set(arguments) - set(specs)

        if extra:
            return {
                "success": False,
                "message": f"存在不支持的参数：{extra}"
            }

        checked = {}

        for key, rule in specs.items():
            value = arguments.get(key, rule["default"])

            # 检查参数类型
            if type(value) is not rule["type"]:
                return {
                    "success": False,
                    "message": f"{key} 参数类型错误"
                }

            # 数值和字符串分别校验。
            if rule["type"] is int:
                if not rule["min"] <= value <= rule["max"]:
                    return {
                        "success": False,
                        "message": f"{key} 超出允许范围"
                    }

            elif rule["type"] is str:
                length = len(value)

                if not rule["min_length"] <= length <= rule["max_length"]:
                    return {
                        "success": False,
                        "message": f"{key} 长度超出允许范围"
                    }

            else:
                return {
                    "success": False,
                    "message": f"{key} 参数类型尚未支持"
                }

            checked[key] = value

        print(
            f"[SkillManager] 调用: {name}, 参数: {checked}"
        )

        try:
            return skill.execute(**checked)
        except Exception as error:
            return {
                "success": False,
                "message": f"技能执行异常：{error}"
            }
