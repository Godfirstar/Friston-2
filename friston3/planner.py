"""Choose one route, then bind it to the registered execution boundary."""
from copy import deepcopy
import json
import math
from time import perf_counter
from urllib.request import Request, urlopen

from friston3.config import OLLAMA_MODEL, OLLAMA_CHAT_URL, OLLAMA_OPTIONS, OLLAMA_KEEP_ALIVE
from friston3.npc.agentic_rag import (
    AgenticSettings, ROUTE_SCHEMA, ROUTE_TOKENS, _unique_object,
    build_route_input, normalize_entity_choices, parse_decision, route_schema_for_signals,
)
from friston3.npc.persona import normalize_presentation
from friston3.skills.volume import VolumeControlSkill
from friston3.skills.device_settings import DeviceSettingsSkill


class LLMPlanner:
    """A single bounded model call. SkillManager retains final execution authority."""

    supports_dialogue_context = True
    TOOL_HELP = {
        "take_photo": "立即拍一张照片；arguments必须为{}。",
        "auto_photo": "自动构图、倒计时拍照；可选整数countdown_seconds，1至10，默认3。",
        "control_volume": VolumeControlSkill.description,
        "configure_device": DeviceSettingsSkill.description,
    }

    def __init__(self, skill_manager, model=OLLAMA_MODEL, url=OLLAMA_CHAT_URL):
        self.model, self.url = model, url
        self.allowed = sorted((set(self.TOOL_HELP) | {"npc_talk"}) & set(skill_manager.skills))
        self.last_call = {}
        self.last_input = []

    @staticmethod
    def _estimate_tokens(text):
        wide = sum(ord(char) >= 0x2e80 for char in text)
        return math.ceil(wide * 1.5 + (len(text) - wide) / 3)

    @staticmethod
    def _schema_for_routes(dialogue_schema, dialogue_routes, action_routes):
        """Keep the generation contract aligned with each execution boundary.

        Keep each anyOf alternative self-contained; do not mix properties
        with anyOf or depend on conditional if/then grammar support.
        Runtime validators remain authoritative even with constrained output.
        """
        def closed_object(properties, required=()):
            return {"type": "object", "properties": properties,
                    "required": list(required), "additionalProperties": False}

        empty_arguments = closed_object({})
        percent = {"type": "integer", "minimum": 0, "maximum": 100}
        argument_schemas = {
            "take_photo": empty_arguments,
            "auto_photo": closed_object({
                "countdown_seconds": {"type": "integer", "minimum": 1, "maximum": 10}}),
            "control_volume": {"anyOf": [
                closed_object({"operation": {"type": "string", "enum": ["set"]},
                               "value": percent}, ("operation", "value")),
                closed_object({"operation": {"type": "string", "enum": ["increase", "decrease"]},
                               "value": percent}, ("operation",)),
                closed_object({"operation": {"type": "string", "enum": ["mute", "unmute", "status"]}},
                              ("operation",)),
            ]},
            "configure_device": {"anyOf": [
                closed_object({"setting": {"type": "string", "enum": [setting]},
                               **({"choice": {"type": "string", "enum": list(choices)}}
                                  if choices else {})},
                              ("setting", "choice") if choices else ("setting",))
                for setting, choices in DeviceSettingsSkill.choices.items()
            ]},
            "UNSUPPORTED": empty_arguments,
        }

        def branch(routes, arguments, *, dialogue=False):
            properties = {
                "route": {"type": "string", "enum": list(routes)},
                "arguments": deepcopy(arguments),
                **{key: deepcopy(value) for key, value in dialogue_schema["properties"].items()
                   if key != "route"},
            }
            if not dialogue:
                properties["entity_choices"]["maxItems"] = 0
                for key, value in (("query", ""), ("reply", ""), ("followup", False),
                                   ("detail", False), ("intent", "general")):
                    properties[key] = {"type": "boolean" if type(value) is bool else "string",
                                       "enum": [value]}
                properties["presentation"] = closed_object({
                    "emotion": {"type": "string", "enum": ["neutral"]},
                    "gesture": {"type": "string", "enum": ["none"]}},
                    ("emotion", "gesture"))
            return closed_object(properties, properties)

        branches = ([branch(dialogue_routes, empty_arguments, dialogue=True)]
                    if dialogue_routes else [])
        branches.extend(branch((route,), argument_schemas[route])
                        for route in [*action_routes, "UNSUPPORTED"])
        return {"anyOf": branches}

    def plan(self, instruction, *, dialogue_context=None):
        self.last_call, self.last_input = {}, []
        if not isinstance(instruction, str) or not instruction.strip():
            return None
        context = dialogue_context or {}
        signals = context.get("signals", {})
        dialogue_schema = route_schema_for_signals(signals)
        dialogue_routes = (tuple(dialogue_schema["properties"]["route"]["enum"])
                           if "npc_talk" in self.allowed else ())
        action_routes = [name for name in self.allowed if name != "npc_talk"]
        # A single discriminator prevents contradictory skill/route pairs.
        # This mapping only binds an explicit model decision; it never infers
        # a hardware action from dialogue, arguments, or a repaired draft.
        allowed_routes = {*dialogue_routes, *action_routes, "UNSUPPORTED"}
        schema = self._schema_for_routes(dialogue_schema, dialogue_routes, action_routes)
        started = perf_counter()
        stage = "build_input"
        self.last_call = {"phase": "unified_plan", "model": self.model,
                          "status": "pending", "request_attempted": False,
                          "response_headers_received": False, "response_received": False}
        try:
            messages = build_route_input(
                instruction, history=context.get("history", []), topic=context.get("topic", {}),
                memory_context=context.get("memory_context", ""),
                signals=context.get("signals", {}), pending_reference=context.get("pending_reference", {}),
                interlocutor_context=context.get("interlocutor"),
                estimate_tokens=self._estimate_tokens,
                tool_help={name: self.TOOL_HELP[name] for name in action_routes})
            self.last_input = messages
            settings = AgenticSettings.defaults()
            self.last_call.update(
                timeout_s=settings.route_timeout, stream=False,
                output_token_limit=ROUTE_TOKENS,
                estimated_prompt_tokens=sum(
                    self._estimate_tokens(m["content"]) + 8 for m in messages))
            payload = {
                "model": self.model, "messages": messages, "format": schema,
                "stream": False, "think": False, "keep_alive": OLLAMA_KEEP_ALIVE,
                "options": {**OLLAMA_OPTIONS, "temperature": 0, "num_predict": ROUTE_TOKENS},
            }
            request = Request(self.url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                              headers={"Content-Type": "application/json"}, method="POST")
            print(f"[LLMPlanner] 统一规划请求超时配置={settings.route_timeout:g}s"
                  "（故障等待上限，不是响应速度目标）")
            stage = "request"
            self.last_call["request_attempted"] = True
            # One request only. Never retry an unknown plan as a device action.
            with urlopen(request, timeout=settings.route_timeout) as response:
                self.last_call["response_headers_received"] = True
                stage = "read_response"
                result = json.load(response)
            self.last_call["response_received"] = True
            stage = "validate_response"
            # Preserve the actual model reply even when parsing or validation
            # fails. Agent already includes last_call in its in-memory evidence.
            if isinstance(result, dict):
                self.last_call["done_reason"] = result.get("done_reason")
                message = result.get("message")
                if isinstance(message, dict) and isinstance(message.get("content"), str):
                    self.last_call["raw_output"] = message["content"]
            if (not isinstance(result, dict) or result.get("error") or result.get("done") is not True
                    or not isinstance(result.get("message"), dict)
                    or not isinstance(result["message"].get("content"), str)
                    or result.get("done_reason") in ("length", "max_tokens", "max_output_tokens", "token_limit")):
                raise ValueError("planner output is missing, failed or truncated")

            def seconds(key):
                value = result.get(key)
                return round(value / 1e9, 3) if isinstance(value, (int, float)) else None

            self.last_call = {
                **self.last_call,
                "phase": "unified_plan", "model": self.model, "status": "ok",
                "wall_s": round(perf_counter() - started, 3),
                "load_s": seconds("load_duration"), "prompt_s": seconds("prompt_eval_duration"),
                "decode_s": seconds("eval_duration"), "prompt_tokens": result.get("prompt_eval_count"),
                "output_tokens": result.get("eval_count"),
            }
            stage = "validate_plan"
            raw = result["message"]["content"]
            call = json.loads(raw, object_pairs_hook=_unique_object)
            self.last_call["parsed_plan"] = deepcopy(call)
            expected = {"arguments", *ROUTE_SCHEMA["required"]}
            if not isinstance(call, dict) or set(call) != expected:
                raise ValueError("planner fields do not match the schema")
            # Presentation remains advisory and cannot grant device authority.
            call["presentation"] = normalize_presentation(call["presentation"])
            route, arguments = call["route"], call["arguments"]
            if not isinstance(route, str) or type(arguments) is not dict:
                raise ValueError("invalid route/argument types")
            if route not in allowed_routes:
                raise ValueError("route is not registered")
            if route in dialogue_routes:
                skill = "npc_talk"
                # Older/permissive decoders can emit this lone no-op choice.
                # Never discard actual settings/actions or rewrite the route;
                # owner_side=none remains a real configure_device operation.
                discarded_noop_arguments = arguments == {"choice": "none"}
                if discarded_noop_arguments:
                    arguments = {}
                    call["arguments"] = arguments
                elif arguments:
                    raise ValueError("dialogue routes may not supply execution arguments")
                decision = parse_decision(json.dumps({key: call[key] for key in ROUTE_SCHEMA["required"]},
                                                     ensure_ascii=False),
                                          signals=signals,
                                          pending_reference=context.get("pending_reference"),
                                          diagnostics=self.last_call)
                decision = normalize_entity_choices(decision, signals, diagnostics=self.last_call)
                if discarded_noop_arguments:
                    self.last_call.setdefault("normalizations", []).append(
                        "discarded_noop_dialogue_arguments")
                    self.last_call["normalized_arguments"] = {}
            else:
                skill = "none" if route == "UNSUPPORTED" else route
                if (call["query"] != "" or call["reply"] != ""
                        or call["entity_choices"] != []
                        or call["followup"] is not False or call["intent"] != "general"
                        or call["detail"] is not False):
                    raise ValueError("non-dialogue routes may not contain dialogue fields")
                if skill == "auto_photo":
                    if set(arguments) - {"countdown_seconds"}:
                        raise ValueError("unknown auto_photo argument")
                    seconds_value = arguments.get("countdown_seconds", 3)
                    if type(seconds_value) is not int or not 1 <= seconds_value <= 10:
                        raise ValueError("countdown_seconds is out of range")
                elif skill == "control_volume":
                    VolumeControlSkill.validate_arguments(arguments)
                elif skill == "configure_device":
                    DeviceSettingsSkill.validate_arguments(arguments)
                elif arguments:
                    raise ValueError("this skill takes no arguments")
                decision = None
            self.last_call["selected_skill"] = skill
            self.last_call["wall_s"] = round(perf_counter() - started, 3)
            print("[LLMPlanner] 统一决策性能：", {key: value for key, value in self.last_call.items()
                  if key not in ("raw_output", "parsed_plan", "normalized_decision")})
            print("[LLMPlanner] 模型原始输出:", raw)
            if skill == "none":
                return None
            answer = {"skill": skill, "arguments": arguments}
            if decision is not None:
                answer["npc_decision"] = decision
            return answer
        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
            timed_out = (isinstance(error, TimeoutError)
                         or isinstance(getattr(error, "reason", None), TimeoutError))
            self.last_call = {**self.last_call, "phase": "unified_plan", "model": self.model,
                              "status": "error", "wall_s": round(perf_counter() - started, 3),
                              "failure_stage": stage,
                              "error_kind": "timeout" if timed_out else "planning_error",
                              "error": f"{type(error).__name__}: {error}"}
            print("[LLMPlanner] 统一决策失败；未执行任何技能：", self.last_call)
            return None
