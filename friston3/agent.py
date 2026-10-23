"""One planning decision, then the existing skill validation/execution boundary."""
from copy import deepcopy
from time import perf_counter
from friston3.input_text import prepare_input_text, is_memory_command


class Agent:
    def __init__(self, skill_manager, planner):
        self.skill_manager, self.planner = skill_manager, planner

    def handle(self, instruction, *, input_source="text", interlocutor=None):
        if not isinstance(instruction, str) or not instruction.strip():
            return {"success": False, "message": "请输入具体内容。"}
        input_text = prepare_input_text(instruction, input_source=input_source)
        instruction = input_text["normalized_text"]
        input_source = input_text["input_source"]
        if len(instruction) > 500:
            return {"success": False, "message": "单次输入请控制在500字以内。"}
        started = perf_counter()
        print(f"[Agent] 收到指令: {instruction}")
        npc = self.skill_manager.skills.get("npc_talk")
        if callable(getattr(npc, "set_interlocutor", None)):
            # Runtime metadata is supplied outside user text and model output.
            # Other callers without a trusted context default to an unknown user.
            npc.set_interlocutor(interlocutor)
        context, routed_to_npc, planning_trace_written = None, False, False
        try:
            # Explicit command syntax remains a protocol, not intent inference.
            if is_memory_command(instruction):
                call = {"skill": "npc_talk", "arguments": {}}
            elif (getattr(self.planner, "supports_dialogue_context", False)
                  and callable(getattr(npc, "prepare_route_context", None))
                  and getattr(getattr(npc, "agentic", None), "enabled", False)):
                context = npc.prepare_route_context(instruction, input_source=input_source)
                context["input_text"] = deepcopy(input_text)
                call = self.planner.plan(instruction, dialogue_context=context)
            else:
                call = self.planner.plan(instruction)
            if call is None:
                planner_call = deepcopy(getattr(self.planner, "last_call", {}))
                error = planner_call.get("status") == "error"
                timed_out = planner_call.get("error_kind") == "timeout"
                reply = ("本轮规划请求超时，尚未执行任何设备动作。输入“查看证据”可查看诊断。"
                         if timed_out else
                         "本轮规划未完成，尚未执行任何设备动作。输入“查看证据”可查看诊断。"
                         if error else "这条指令暂不能执行；没有调用设备。")
                # The CLI reads npc.last_trace. Record failures before NPC.execute
                # too, without writing them into conversation or long-term memory.
                if npc is not None and isinstance(getattr(npc, "last_trace", None), dict):
                    attempted = bool(planner_call.get("request_attempted"))
                    calls = [planner_call] if attempted else []
                    signals = (context or {}).get("signals", {})
                    elapsed = round(perf_counter() - started, 3)
                    npc.last_trace = {
                        "question": input_text["raw_text"],
                        "normalized_question": instruction,
                        "input_text": deepcopy(input_text),
                        "route": "planning_failed" if error else "unsupported_skill",
                        "output_origin": "planner_failure" if error else "unsupported_skill",
                        "model_called": attempted, "generation_accepted": False,
                        "fallback_used": False, "execution_started": False,
                        "reply": reply, "subjects": [], "sent_sources": [],
                        "retrieved_source_ids": [], "used_source_ids": [],
                        "semantic_review_performed": False,
                        "model_input": deepcopy(getattr(self.planner, "last_input", [])),
                        "planner_call": planner_call, "ollama_calls": calls,
                        "agentic_rag": {
                            "decision": "UNAVAILABLE" if error else "UNSUPPORTED",
                            "reason": "planner_timeout" if timed_out else
                                      "planner_failure" if error else "unsupported_skill",
                            "model_decision": None,
                            "semantic": deepcopy(signals.get("semantic_evidence_demand", {})),
                            "entity_resolution": deepcopy(signals.get("entity_resolution", {})),
                            "retrieval_calls": 0, "model_calls": len(calls),
                            "model_phases": [c["phase"] for c in calls], "elapsed_s": elapsed,
                        },
                        "elapsed_s": elapsed,
                    }
                    planning_trace_written = True
                return {"success": False, "message": reply}
            if call["skill"] == "npc_talk":
                routed_to_npc = True
                if callable(getattr(npc, "accept_input_context", None)):
                    npc.accept_input_context(input_text)
                # Internal one-shot handoff: never expose model routing fields as
                # public skill arguments or bypass SkillManager's validation.
                if (context is not None and call.get("npc_decision") is not None
                        and callable(getattr(npc, "accept_route_plan", None))):
                    npc.accept_route_plan(instruction, call["npc_decision"], context,
                                          getattr(self.planner, "last_input", []),
                                          getattr(self.planner, "last_call", {}), started)
                call = {"skill": "npc_talk", "arguments": {"user_text": instruction}}
            print(f"[Agent] 工具调用: {call}")
            return self.skill_manager.execute(call["skill"], call["arguments"])
        finally:
            if callable(getattr(npc, "discard_route_plan", None)):
                npc.discard_route_plan()
            if callable(getattr(npc, "discard_input_context", None)):
                npc.discard_input_context()
            elapsed = round(perf_counter() - started, 3)
            if (routed_to_npc or planning_trace_written) and isinstance(getattr(npc, "last_trace", None), dict):
                if hasattr(npc, "interlocutor"):
                    npc.last_trace["interlocutor"] = deepcopy(npc.interlocutor)
                npc.last_trace["agent_elapsed_s"] = elapsed
                if planning_trace_written:
                    npc.last_trace["elapsed_s"] = elapsed
                    npc.last_trace["agentic_rag"]["elapsed_s"] = elapsed
            print(f"[Agent] 本轮总耗时 {elapsed:.3f}s（含规划与执行，不含语音合成/播放）")
