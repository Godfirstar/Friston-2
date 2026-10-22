"""普通摄影 Skill：只调用摄像头 Adapter。"""

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
