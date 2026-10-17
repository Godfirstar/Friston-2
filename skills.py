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
    """NPC 角色对话；本课先输出文本。"""

    name = "npc_talk"

    parameters = {
        "reply": {
            "type": str,
            "default": "",
            "min_length": 1,
            "max_length": 120
        }
    }

    def execute(self, reply):
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
