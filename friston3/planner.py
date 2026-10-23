"""Choose one registered skill and, for NPC dialogue, one evidence-access plan."""
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


class LLMPlanner:
    """A single bounded model call. SkillManager retains final execution authority."""

    supports_dialogue_context = True
    TOOL_HELP = {
        "take_photo": "立即拍一张照片；arguments必须为{}。",
        "auto_photo": "自动构图、倒计时拍照；可选整数countdown_seconds，1至10，默认3。",
        "npc_talk": "对话和知识问答；arguments必须为{}，同时决定所需证据和回复。",
    }

    def __init__(self, skill_manager, model=OLLAMA_MODEL, url=OLLAMA_CHAT_URL):
        self.model, self.url = model, url
        self.allowed = sorted(set(self.TOOL_HELP) & set(skill_manager.skills))
        self.last_call = {}
        self.last_input = []

    @staticmethod
    def _estimate_tokens(text):
        wide = sum(ord(char) >= 0x2e80 for char in text)
        return math.ceil(wide * 1.5 + (len(text) - wide) / 3)

    def plan(self, instruction, *, dialogue_context=None):
        self.last_call, self.last_input = {}, []
        if not isinstance(instruction, str) or not instruction.strip():
            return None
        context = dialogue_context or {}
        signals = context.get("signals", {})
        schema = route_schema_for_signals(signals)
        schema["properties"]["route"]["enum"].append("ACTION")
        # A protocol-level whitelist, not a natural-language keyword router.
        schema["properties"] = {
            "skill": {"type": "string", "enum": self.allowed + ["none"]},
            "arguments": {"type": "object", "properties": {
                "countdown_seconds": {"type": "integer", "minimum": 1, "maximum": 10}},
                "additionalProperties": False},
            **schema["properties"],
        }
        schema["required"] = ["skill", "arguments", *schema["required"]]
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
                tool_help={name: self.TOOL_HELP[name] for name in self.allowed})
            self.last_input = messages
            settings = AgenticSettings.from_env()
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
            expected = set(schema["required"])
            if (not isinstance(call, dict) or set(call) - expected
                    or expected - set(call) - {"presentation"}):
                raise ValueError("planner fields do not match the schema")
            # Performance is advisory. A missing field must not break an
            # otherwise valid legacy plan, nor become an extra device action.
            call["presentation"] = normalize_presentation(call.get("presentation"))
            skill, arguments = call["skill"], call["arguments"]
            if not isinstance(skill, str) or type(arguments) is not dict:
                raise ValueError("invalid skill/argument types")
            if skill not in self.allowed + ["none"]:
                raise ValueError("skill is not registered")
            if skill == "npc_talk":
                if arguments:
                    raise ValueError("npc_talk may not supply execution arguments")
                decision = parse_decision(json.dumps({key: call[key] for key in ROUTE_SCHEMA["required"]},
                                                     ensure_ascii=False),
                                          signals=signals,
                                          pending_reference=context.get("pending_reference"),
                                          diagnostics=self.last_call)
                decision = normalize_entity_choices(decision, signals, diagnostics=self.last_call)
            else:
                if (call["route"] != "ACTION" or call["query"] != "" or call["reply"] != ""
                        or call["entity_choices"] != []
                        or call["followup"] is not False or call["intent"] != "general"
                        or call["detail"] is not False):
                    raise ValueError("hardware action may not contain dialogue claims")
                if skill == "auto_photo":
                    if set(arguments) - {"countdown_seconds"}:
                        raise ValueError("unknown auto_photo argument")
                    seconds_value = arguments.get("countdown_seconds", 3)
                    if type(seconds_value) is not int or not 1 <= seconds_value <= 10:
                        raise ValueError("countdown_seconds is out of range")
                elif arguments:
                    raise ValueError("this skill takes no arguments")
                decision = None
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
