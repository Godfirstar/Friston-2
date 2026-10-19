"""Bounded real conversation, trusted identity and source-owned lore evidence."""
import json
import math
import re
import sqlite3
from collections import deque
from difflib import SequenceMatcher
from time import perf_counter
from urllib.request import Request, urlopen

from friston2.config import (OLLAMA_MODEL, OLLAMA_CHAT_URL, NPC_CONTEXT_TOKENS,
                            NPC_REPLY_TOKENS, NPC_HISTORY_TURNS, NPC_HISTORY_TOKEN_BUDGET, KNOWLEDGE_DB)
from friston2.knowledge.terra_kb import retrieve_packet, reply_entity_mentions
from friston2.npc.dialogue import (EXPLANATIONS, bare_text, discourse_act,
                                 reference_description, conversation_act)
from friston2.npc.identity import IDENTITY, SELF_SUBJECT, IDENTITY_SOURCE
from friston2.npc.memory import MemoryStore
from friston2.npc.persona import NPC_SYSTEM_PROMPT, NPC_CHAT_PROMPT, relevant_persona_context, persona_evidence
from friston2.npc.response_guard import classify_self_question, clean_reply, validate_reply


class NpcTalkSkill:
    name = "npc_talk"
    parameters = {
        "user_text": {"type": str, "default": "", "min_length": 1, "max_length": 500}
    }

    def __init__(self, model=OLLAMA_MODEL, url=OLLAMA_CHAT_URL, *, memory_path=None, db_file=KNOWLEDGE_DB):
        self.model = model
        self.url = url
        self.db_file = db_file
        self.history = deque(maxlen=2 * NPC_HISTORY_TURNS)
        self.history_topics = deque(maxlen=NPC_HISTORY_TURNS)
        self.last_entities = []
        self.topic_state = {}
        self.pending_reference = {}
        self.last_trace = {}
        try:
            self.memory = MemoryStore(memory_path)
        except (OSError, sqlite3.Error) as error:
            self.memory = None
            print(f"[NpcTalkSkill] 长期记忆不可用，本次仅保留短期话题：{error}")

    def clear_history(self):
        """Clear topic/audit state, preserving explicitly saved memories."""
        self.history.clear()
        self.history_topics.clear()
        self.last_entities.clear()
        self.topic_state.clear()
        self.pending_reference.clear()
        self.last_trace.clear()

    def clear_all_memory(self):
        if self.memory is None:
            raise RuntimeError("长期记忆文件未成功打开，无法确认已经清空")
        counts = self.memory.clear_all()
        self.clear_history()
        return counts

    def record_execution(self, skill_name, result):
        if self.memory is None:
            return
        try:
            self.memory.record_execution(skill_name, result)
        except (OSError, sqlite3.Error) as error:
            print(f"[NpcTalkSkill] 执行记录未保存：{error}")

    def explain_last_response(self):
        if not self.last_trace:
            return "本次会话还没有可查看的回复依据。"
        return "【上轮实际输入、回复校验与处理路径；规则通过不等于逐句事实正确】\n" + json.dumps(
            self.last_trace, ensure_ascii=False, indent=2)

    @staticmethod
    def _asks_for_live_budget(user_text):
        subjects = ("经费", "工程部预算", "研发预算", "罗德岛预算", "工程部账本", "研发账本", "龙门币余额")
        intents = ("还剩", "剩余", "剩下", "余额", "结余", "多少", "够不够", "查账", "查看", "实时", "核对账目")
        return (any(s in user_text for s in subjects) and any(i in user_text for i in intents)
                or bare_text(user_text) in ('经费如何', '经费怎么样', '预算如何', '预算怎么样'))

    @staticmethod
    def _real_history_kind(user_text, kind):
        """Past physical events belong to execution records, even if asserted by a user."""
        past = re.search(r'刚才|刚刚|上次|昨天|前天|已经|之前|实际|现实', user_text)
        device = re.search(r'你|我们|这台设备|这个终端', user_text)
        if past and device:
            if re.search(r'拍了|拍过|拍好|拍完|拍到|拍摄了|拍摄过|拍照.{0,8}(?:成功|完成|失败)', user_text):
                return 'self_task_history'
            if re.search(r'见面|会面|见过|见了|遇见|遇到|接待', user_text):
                return 'self_encounter'
            if re.search(r'去过|去了|到过|旅行|出差', user_text):
                return 'self_travel'
        if kind == 'self_task_history' or (kind.startswith('self_') and
                any(w in user_text for w in ('这台设备', '现实', '实际', '上次', '刚才', '昨天', '今天', '我们'))):
            return kind
        return ''

    @staticmethod
    def _estimate_tokens(text):
        # Estimate only; actual counts are shown when Ollama returns them.
        wide = sum(ord(char) >= 0x2e80 for char in text)
        return math.ceil(wide * 1.5 + (len(text) - wide) / 3)

    @classmethod
    def _whole_lines(cls, text, budget):
        lines = []
        for line in text.splitlines():
            cost = cls._estimate_tokens(line) + 2
            if line and cost <= budget:
                lines.append(line)
                budget -= cost
        return "\n".join(lines)

    def _retrieval_question(self, user_text):
        """Bare follow-ups keep the user's task, never a generated assertion."""
        bare = bare_text(user_text)
        vague = bare in EXPLANATIONS
        elliptical = bool(re.fullmatch(r"那.{1,35}呢", bare))
        if not self.topic_state or not (vague or elliptical):
            return user_text
        intent = self.topic_state.get("intent")
        suffix = {"personality": "的性格与看法", "introduction": "的介绍",
                  "leadership": "的领袖与负责人", "relationship": "的关系",
                  "experience": "的经历", "concept": "的定义"}.get(intent, "")
        if not suffix:
            return user_text
        # Previous names must not leak into an explicit new-subject query.
        subject = user_text if elliptical else "、".join(
            item["name"] for item in self.topic_state.get("subjects", []))
        return subject + suffix if subject else user_text

    def _clarified_question(self, user_text, packet):
        """Resolve a bare selection in the original task, preserving both endpoints.

        An explicit new question must keep its own intent. Only bounded name
        selections are substituted; neither model text nor an unrelated earlier
        topic can supply a query rewrite.
        """
        if not self.pending_reference or packet.get('resolution', {}).get('ambiguities'):
            return None
        pending_ids = set(self.pending_reference.get('candidate_ids', []))
        choices = [item for item in packet.get('resolution', {}).get('mentions', [])
                   if not pending_ids or item['id'] in pending_ids]
        if len({item['id'] for item in choices}) != 1:
            return None
        aliases = '|'.join(re.escape(bare_text(item['alias'])) for item in choices)
        if not re.fullmatch(r'(?:我(?:说|指)(?:的)?(?:是)?|是|应该是|就是|选择)?(?:' + aliases
                            + r')(?:吧|呀|啊|呢|才对|对吧)?', bare_text(user_text)):
            return None
        selected = choices[0]
        original = self.pending_reference['question']
        rewritten = original
        for ambiguity in self.pending_reference.get('ambiguities', []):
            if any(candidate['id'] == selected['id'] for candidate in ambiguity['candidates']):
                rewritten = rewritten.replace(ambiguity['alias'], selected['name'])
        description = self.pending_reference.get('description')
        if description and not pending_ids:
            rewritten = rewritten.replace(description, selected['name'])
        return rewritten if rewritten != original else None

    @staticmethod
    def _topic_key(subjects):
        """The speaker's presence does not change the person being discussed."""
        ids = {s['id'] if isinstance(s, dict) else s for s in subjects}
        return frozenset(ids - {IDENTITY.entity_id} or ids)

    def _remember(self, user_text, reply, packet=None, sent_sources=(), *, persist=True):
        self.history.append({"role": "user", "content": user_text})
        self.history.append({"role": "assistant", "content": reply})
        packet = packet or {}
        subjects = packet.get("subjects", [])
        acknowledgement = (conversation_act(user_text) == 'thanks' or
                           bare_text(user_text) in ('好的', '好', '哈哈', '嗯', '明白了'))
        keep_topic = not subjects and acknowledgement and bool(self.topic_state)
        if not subjects and acknowledgement:
            subjects = self.topic_state.get('subjects', [])
        self.history_topics.append(self._topic_key(subjects))
        self.last_entities = [item["id"] for item in subjects if item["id"] != IDENTITY.entity_id]
        if any(item["id"] == IDENTITY.entity_id for item in subjects):
            self.last_entities.append(IDENTITY.entity_id)
        if keep_topic:
            pass  # A social acknowledgement does not replace the pending lore task/evidence.
        elif subjects:
            same_topic = self._topic_key(subjects) == self._topic_key(self.topic_state.get('subjects', []))
            # Preserve only a selector for trusted configuration, never model prose.
            persona_hint = self.topic_state.get("persona_hint", "") if same_topic else ""
            self.topic_state = {
                "question": user_text, "intent": packet.get("intent", "general"),
                "subjects": subjects, "source_ids": [item["id"] for item in sent_sources],
                "sources": list(sent_sources),
                "persona_hint": (persona_hint or user_text) if packet.get("intent") == "personality" else "",
            }
        elif not acknowledgement:
            self.topic_state.clear()
        else:
            self.last_entities = [item["id"] for item in self.topic_state.get("subjects", [])]
        if self.memory is not None and persist:
            try:
                self.memory.log_turn(user_text, reply)
            except (OSError, sqlite3.Error) as error:
                print(f"[NpcTalkSkill] 对话记录未保存：{error}")

    def _direct_reply(self, user_text, reply, route, subjects=(), records=(), sources=(), **trace):
        """Only real device/memory status may bypass dialogue generation."""
        packet = {"subjects": list(subjects), "intent": route}
        self._remember(user_text, reply, packet, sources)
        self.pending_reference.clear()
        self.last_trace = {
            "question": user_text, "route": route, "model_called": False,
            "generation_accepted": False, "output_origin": "execution_record", "fallback_used": False,
            "subjects": list(subjects), "sent_sources": [], "used_sources": list(sources),
            "history_sent": [], "raw_assistant_history_sent": False,
            "identity_source": IDENTITY_SOURCE,
            "rule_source": "friston2/skills/npc.py",
            "execution_records": list(records), "reply": reply, **trace,
        }
        return {"success": True, "message": reply}

    def _memory_command(self, user_text):
        match = re.fullmatch(r"\s*(记住|忘记)\s*[:：]\s*(.*?)\s*", user_text, flags=re.S)
        if match is None:
            return None
        action, statement = match.groups()
        if not statement:
            return {"success": False, "message": f"请在“{action}：”后写下具体内容。"}
        if self.memory is None:
            return {"success": False, "message": "长期记忆文件未成功打开，这条内容还没有保存或删除。"}
        try:
            if action == "记住":
                self.memory.remember(statement)
                reply = "记下了，这是你告诉我的信息。以后相关时我会参考它。"
                self._remember(user_text, reply)
                self.pending_reference.clear()
            else:
                count = self.memory.forget(statement)
                self.clear_history()
                reply = f"已删除 {count} 条匹配的长期记忆，并清空本次短期对话。" if count else "没有找到匹配的长期记忆；本次短期对话已清空。"
            self.last_trace = {"route": "explicit_memory_command", "model_called": False, "reply": reply,
                               "generation_accepted": False, "output_origin": "memory_command", "fallback_used": False}
            return {"success": True, "message": reply}
        except (OSError, sqlite3.Error) as error:
            return {"success": False, "message": f"记忆操作未完成：{error}"}

    def _self_history_reply(self, user_text, kind):
        subjects = [dict(SELF_SUBJECT)]
        if kind in ("self_encounter", "self_travel"):
            # Camera events and user reports prove neither a meeting nor a journey.
            thing = "会面" if kind == "self_encounter" else "出行"
            return self._direct_reply(user_text, f"我这里没有可核实的{thing}记录。资料里的故事我能聊，但不能说成自己亲历过。", kind, subjects)
        if self.memory is None:
            return self._direct_reply(user_text, "执行记录现在读不到，我不能确认自己做过哪些任务。", kind, subjects)
        try:
            events = self.memory.recent_execution_events(limit=1)
        except (OSError, sqlite3.Error) as error:
            print(f"[NpcTalkSkill] 执行记录读取失败：{error}")
            return self._direct_reply(user_text, "执行记录现在读不到，我不能确认自己做过哪些任务。", kind, subjects)
        if not events:
            return self._direct_reply(user_text, "暂时没有保存下来的真实任务记录。我能陪你聊已有资料，但不会把剧情说成自己的往事。", kind, subjects)
        event = events[0]
        label = "自动拍照" if event["skill"] == "auto_photo" else "普通拍照"
        outcome = "成功" if event["success"] else "失败"
        reply = f"最近保存的一次执行记录是{label}，结果为{outcome}。"
        return self._direct_reply(user_text, reply, kind, subjects, events)

    def _recent_messages(self, budget, *, subjects=None, clarification=False):
        """Keep a contiguous suffix of complete user/assistant pairs."""
        history = list(self.history)
        selected = []
        for index in range(len(history) - 2, -1, -2):
            if (subjects is not None and self.history_topics[index // 2] != self._topic_key(subjects)
                    and not (clarification and index == len(history) - 2)):
                break
            pair = history[index:index + 2]
            if [m['role'] for m in pair] != ['user', 'assistant']:
                break
            cost = sum(self._estimate_tokens(m['content']) + 8 for m in pair)
            if cost > budget:
                break  # Never skip the latest turn and send misleading older context.
            selected[0:0] = [dict(m) for m in pair]
            budget -= cost
            if clarification:
                break  # Only the immediately preceding ambiguous question belongs here.
        return selected

    def _messages(self, user_text, packet, memory_context, act="question"):
        ids = packet.get("entity_ids", [])
        previous_ids = {item["id"] for item in self.topic_state.get("subjects", [])}
        current_ids = {item["id"] for item in packet.get("subjects", [])}
        same_topic = bool(current_ids and self._topic_key(current_ids) == self._topic_key(previous_ids))
        ambiguities = packet.get('resolution', {}).get('ambiguities', [])
        social_act = packet.get('conversation_act', conversation_act(user_text))
        selector = user_text
        if same_topic and packet["intent"] == "personality":
            selector += "\n" + self.topic_state.get("persona_hint", "")
        persona = self._whole_lines(relevant_persona_context(selector, ids), 300)
        ordinary_chat = (not current_ids and not ambiguities
                         and not packet.get('missing_knowledge')
                         and not packet.get('sources'))
        if ambiguities or packet.get('reference_description'):
            system = ('你是Friston，正在与用户交谈。现在只需澄清一个不明确的称呼。'
                      '候选是不同的人，不是同一人的不同名字。只生成一个询问指代的短问句，不介绍人物、不编造关系。')
        else:
            system = (NPC_CHAT_PROMPT if ordinary_chat else NPC_SYSTEM_PROMPT)
        system += "\n\n" + persona if persona else ""
        state = {"当前话题": [item["name"] for item in packet["subjects"]],
                 "对话动作": act, "当前意图": packet["intent"],
                 "日常交流类型": social_act, "沿用主体": same_topic}
        if packet.get('resolution', {}).get('mentions'):
            state['本轮称呼解析'] = [
                {'称呼': item['alias'], '人物': item['name']}
                for item in packet['resolution']['mentions']]
        topic = "【当前讨论对象，不是说话人身份】\n" + json.dumps(state, ensure_ascii=False)
        system += "\n\n" + topic
        if ambiguities:
            choices = [{'称呼': a['alias'], '候选': [c['name'] for c in a['candidates']]}
                       for a in ambiguities]
            system += ('\n【待澄清称呼】' + json.dumps(choices, ensure_ascii=False) +
                       '\n这个称呼对应多个候选。用一句简短问句列出候选，请用户明确指谁；'
                       '候选不是人物事迹或现任职务的证据，不要选择其中一位、介绍或评价候选。')
        elif packet.get('reference_description'):
            system += ('\n尚未确定这个外观称呼指谁：' + packet['reference_description'] +
                       '。只询问名字或能区分人物的细节，不猜人物身份。')
        elif packet.get('missing_knowledge'):
            system += '\n本轮没有找到问题所需资料。说明具体缺口，不编造答案，也不要责备提问者。'
        if packet.get('live_budget_unavailable'):
            system += '\n真实账本没有接入，不能提供实时余额或假装已查询；简短说明这一点。'
        memory = self._whole_lines(memory_context, 220)
        if memory:
            system += "\n\n【用户自述与执行结果；用户自述不证明原作事实或设备亲历】\n" + memory
        # Reserve generation + framing/token-estimation slack. Evidence and
        # recent dialogue have separate budgets so neither silently takes all.
        prompt_limit = NPC_CONTEXT_TOKENS - NPC_REPLY_TOKENS - 350
        budget = max(0, prompt_limit - self._estimate_tokens(system) - self._estimate_tokens(user_text) - 24)
        sent_sources = []
        curated = [] if ordinary_chat or ambiguities else persona_evidence(selector, ids)
        if IDENTITY.entity_id not in ids:
            curated = [source for source in curated if source['id'] != 'canon:friston:identity']
        personal_view = (packet['intent'] in ('personality', 'relationship', 'introduction') and
                         any(s.get('kind') == 'relation' for s in curated))
        personal_view = personal_view or current_ids == {IDENTITY.entity_id}
        candidates = curated + ([] if personal_view else list(packet.get("sources", [])))
        primary_ids = current_ids - {IDENTITY.entity_id}
        # A short introduction needs the person's own overview, not every
        # neighbour or event in the graph. This also prevents quoted dialogue
        # from a related story being mistaken for the NPC's current identity.
        if not personal_view and len(primary_ids) == 1:
            own = [s for s in packet.get('sources', []) if s.get('subject_id') in primary_ids]
            overviews = [s for s in own if s.get('kind') == 'overview']
            if packet['intent'] == 'introduction' and overviews:
                candidates = curated + overviews[:1]
            elif packet['intent'] == 'personality' and overviews:
                judgments = [s for s in own if s.get('kind') != 'relation' and
                             any(w in s.get('section', '') for w in ('点评', '性格', '评价'))]
                candidates = curated + overviews[:1] + judgments[:1]
            elif packet['intent'] == 'experience':
                # A passing mention in another person's biography is not this
                # person's experience, especially after passage truncation.
                candidates = curated + own
        # A missing self/other relationship does not erase the other person's
        # overview. It may explain who they are, while not proving acquaintance.
        if IDENTITY.entity_id in ids and len(ids) > 1 and not personal_view:
            candidates += list(packet.get('target_overviews', []))
        bare = bare_text(user_text)
        if (same_topic
                and packet["intent"] == self.topic_state.get("intent")
                and bare in EXPLANATIONS):
            previous = [{**source, "reused_from_previous_input": True}
                        for source in self.topic_state.get("sources", [])]
            seen = {source["id"] for source in previous}
            candidates = previous + [source for source in candidates if source["id"] not in seen]
        evidence_reserve = min(900, sum(self._estimate_tokens(s['block']) + 2 for s in candidates))
        history_subjects = previous_ids if social_act == 'thanks' and not current_ids else current_ids
        history = self._recent_messages(
            min(NPC_HISTORY_TOKEN_BUDGET, max(0, budget - evidence_reserve)),
            subjects=history_subjects, clarification=packet.get('clarification_resolved', False))
        budget -= sum(self._estimate_tokens(m['content']) + 8 for m in history)
        for source in candidates:
            if any(item['id'] == source['id'] for item in sent_sources):
                continue
            cost = self._estimate_tokens(source["block"]) + 2
            if cost <= budget:
                sent_sources.append(source)
                budget -= cost
        evidence = "\n".join(source["block"] for source in sent_sources)
        if ambiguities or packet.get('reference_description'):
            pass  # No contradictory invitation to introduce unknown candidates.
        elif evidence:
            system += "\n\n【本轮资料；每块主体独立，不得互换】\n" + evidence
        elif packet.get("subjects"):
            system += "\n本轮预算内没有可用人物证据；不要补造身份、关系或剧情。"
        else:
            system += "\n本轮没有检索到世界观事实；可以普通聊天，不要添加人物身份、往事或任务执行结果。"
        if IDENTITY.entity_id in ids and len(ids) > 1 and not any(s.get('kind') == 'relation' for s in sent_sources):
            system += '\n本轮没有你与对方直接往来的依据。可以说知道这个名字或了解资料，不能说认识、从未见过，或编造交情。'
        primary_names = [s['name'] for s in packet['subjects'] if s['id'] != IDENTITY.entity_id]
        if primary_names:
            system += ('\n当前人物是' + '、'.join(primary_names) +
                       '。明确承接这个对象；前文指代清楚时可以说他、她或它，无需每次重复全名。')
        if act == 'explanation':
            system += '\n用户在追问上一句判断的理由。解释那个判断，不重新自我介绍或复述整段档案。'
        elif act == 'correction' and same_topic:
            system += '\n用户在确认当前讨论的人物，简短承接这次确认即可，不重复整份人物介绍，不把名字当成用户或你的身份。'
        if packet.get('intent') == 'introduction' and not ambiguities:
            system += '\n了解人物资料不等于私人相识。可以介绍所知，但不要凭资料声称亲历或交情。'
        messages = [{"role": "system", "content": system}, *history,
                    {"role": "user", "content": user_text}]
        return messages, sent_sources, {"topic_context": topic, "persona_context": persona,
            "memory_context": memory, "history_sent": history,
            "history_turns_sent": len(history) // 2,
            "history_turns_omitted": (len(self.history) - len(history)) // 2,
            "raw_assistant_history_sent": bool(history),
            "identity_source": IDENTITY_SOURCE, "context_limit": NPC_CONTEXT_TOKENS}

    def _draft_issues(self, reply, packet, sent_sources):
        mentions = reply_entity_mentions(reply, db_file=self.db_file) if reply else []
        candidates = [c for a in packet.get('resolution', {}).get('ambiguities', [])
                      for c in a['candidates']]
        issues = validate_reply(reply, packet['subjects'], sent_sources, mentions,
                                user_text=self.last_trace.get('question', ''),
                                act=packet.get('conversation_act', ''), candidate_entities=candidates)
        if candidates and not (all(c['name'] in reply for c in candidates)
                               and re.search(r'谁|哪位|哪个|还是|[？?]', reply)):
            issues.append('missing_reference_clarification')
        support = '\n'.join(source['text'] for source in sent_sources)
        for term in ('机械臂', '神经毒素'):
            if term in reply and term not in support:
                issues.append('unsupported_topic_detail:' + term)
        previous = self.history[-1]['content'] if self.history else ''
        previous_question = self.history[-2]['content'] if len(self.history) >= 2 else ''
        current_act = packet.get('conversation_act', 'general')
        same_social_act = (current_act != 'general' and
                           current_act == conversation_act(previous_question))
        # Two greetings (including a language switch) can naturally receive
        # similar greetings. Repetition alone must not block harmless social
        # turns; copying praise into an unrelated emotional turn is different.
        if (not same_social_act and len(reply) > 35 and reply == previous and
                self.last_trace.get('question') != previous_question):
            issues.append('repeated_previous_reply')
        for old in self.history:
            if same_social_act or old['role'] != 'assistant' or len(reply) < 40:
                continue
            match = SequenceMatcher(None, old['content'], reply, autojunk=False).find_longest_match()
            if match.size >= 30 and match.size / len(reply) > 0.55:
                issues.append('repeated_previous_reply')
                break
        return list(dict.fromkeys(issues))

    @staticmethod
    def _repair_guidance(issues, packet):
        """Describe actionable corrections, rather than exposing opaque rule IDs."""
        if packet.get('resolution', {}).get('ambiguities'):
            return ('只提出含全部候选名字的简短选择问句，等待用户明确指谁。'
                    '不添加人物职位、关系或事迹，也不省略候选名字。')
        hints = []
        for issue in issues:
            if issue.startswith(('unsupported_named_entity', 'unassigned_user_role')):
                hint = '不要给用户安上博士等角色称呼；不主动加入缺少依据的人名。'
            elif issue.startswith(('unsupported_self_', 'unsupported_personal_', 'unsupported_negative_')):
                hint = '不要把资料或评价说成亲历、相识或从未见过；直接谈当前对象的已知情况。'
            elif issue.startswith('unsupported_role_or_relation'):
                hint = '人物身份与关系只取本轮对应人物证据，不增添职位、关系或种族。'
            elif issue.startswith('unsupported_device'):
                hint = '没有真实执行结果，不能声称刚完成拍照、递物等动作。'
            elif issue == 'unsupported_stage_action':
                hint = '只写终端说给用户听的话，删除所有身体动作、神态和场景旁白，包括拍肩、拍桌和点头。'
            elif issue == 'missing_reference_clarification':
                hint = '只提出含全部候选名字的简短选择问句，等待用户明确指谁。'
            elif issue == 'repeated_previous_reply':
                hint = '回答这次的追问或理由，不重复上一轮整段内容。'
            elif issue == 'empty_or_incomplete_reply':
                hint = '用一至两句简短完整的话回答，控制在100字内，避免输出被截断。'
            else:
                hint = '认真回应用户当前的话；不要训斥、贬低或揣测用户，也不要写旁白。'
            if hint not in hints:
                hints.append(hint)
        if packet.get('conversation_act') in ('greeting', 'thanks', 'compliment', 'emotion'):
            hints.append('这是日常交流，不是事实核查；自然回应，不用“缺少依据”拒绝交流。')
        return ''.join(hints)[:400]

    def execute(self, user_text):
        self.last_trace = {"question": user_text, "route": "pending", "model_called": False}
        command_result = self._memory_command(user_text)
        if command_result is not None:
            return command_result
        kind = classify_self_question(user_text)
        real_history = self._real_history_kind(user_text, kind)
        if real_history:
            return self._self_history_reply(user_text, real_history)
        if (bare_text(user_text) in EXPLANATIONS and
                self.topic_state.get('intent') in ('self_encounter', 'self_travel')):
            return self._self_history_reply(user_text, self.topic_state['intent'])

        retrieval_question = (f"Friston-3 的经历：{user_text}" if kind.startswith('self_')
                              else self._retrieval_question(user_text))
        try:
            packet = retrieve_packet(user_text, previous_entities=self.last_entities,
                                     retrieval_question=retrieval_question, max_chars=1500, db_file=self.db_file)
            clarified_question = self._clarified_question(user_text, packet)
            if clarified_question:
                selection_resolution = packet['resolution']
                retrieval_question = clarified_question
                packet = retrieve_packet(clarified_question, max_chars=1500, db_file=self.db_file)
                packet['resolution'] = {**selection_resolution, 'entity_ids': packet['entity_ids'],
                                        'clarified_question': clarified_question}
        except (OSError, sqlite3.Error, RuntimeError) as error:
            self.last_trace.update(route='retrieval_error', error=str(error))
            return {'success': False, 'message': f'知识资料读取失败：{error}'}
        # Previously these requests returned pre-written roleplay answers. All
        # lore and social dialogue now uses actual model generation and the same
        # validation path; only explicit memory/device records bypass it.
        if kind and not packet['subjects']:
            packet['subjects'] = [dict(SELF_SUBJECT)]
            packet['entity_ids'] = [IDENTITY.entity_id]
        if kind == 'identity':
            packet['intent'] = 'introduction'
        elif kind in ('preferences', 'favorite_person', 'trusted_person'):
            packet['intent'] = 'personality'
        packet['conversation_act'] = conversation_act(user_text)
        packet['live_budget_unavailable'] = self._asks_for_live_budget(user_text)
        packet['missing_knowledge'] = (not packet['sources'] and not packet['subjects']
                                       and (packet['intent'] != 'general' or packet.get('lore_question'))
                                       and packet['conversation_act'] == 'general')
        print(f"[NpcTalkSkill] 图谱命中实体: {packet.get('entity_ids') or '无'}")
        ambiguities = packet.get('resolution', {}).get('ambiguities', [])
        previous_question = self.history[-2]['content'] if len(self.history) >= 2 else ''
        act = discourse_act(user_text, packet['subjects'], previous_question, self.pending_reference)
        pending_ids = set(self.pending_reference.get('candidate_ids', []))
        current_ids = set(packet.get('entity_ids', []))
        packet['clarification_resolved'] = bool(self.pending_reference and current_ids and not ambiguities and
                                                (not pending_ids or current_ids & pending_ids))
        if packet['clarification_resolved']:
            act = 'correction'
        elif (act == 'correction' and packet['intent'] == 'general' and
              self._topic_key(packet['subjects']) == self._topic_key(self.topic_state.get('subjects', []))):
            packet['intent'] = self.topic_state.get('intent', 'general')
        description = reference_description(user_text, packet['subjects'])
        packet['reference_description'] = description
        next_pending = {}
        if ambiguities or description:
            next_pending = {'question': user_text, 'description': description, 'intent': packet['intent'],
                            'candidate_ids': [c['id'] for a in ambiguities for c in a['candidates']],
                            'ambiguities': ambiguities}
            act = 'clarification'
        memory_context = ""
        if self.memory is not None:
            try:
                # Camera outcomes are handled by the self-history branch. They
                # must not enter an unrelated character's biography via '经历'.
                memory_context = self.memory.recall(user_text, include_execution=False)
            except (OSError, sqlite3.Error) as error:
                print(f"[NpcTalkSkill] 互动记忆读取失败：{error}")
        messages, sent_sources, contexts = self._messages(user_text, packet, memory_context, act)
        if sent_sources:
            print(f"[NpcTalkSkill] 已提供 {len(sent_sources)} 条带主体与出处的证据")
        self.last_trace = {
            "question": user_text, "retrieval_question": retrieval_question, "route": "grounded_dialogue",
            "intent": packet["intent"], "subjects": packet["subjects"], "model_called": False,
            "resolution": packet.get('resolution', {}),
            "conversation_act": packet['conversation_act'],
            "clarification_resolved": packet['clarification_resolved'],
            "generation_accepted": False, "output_origin": "pending", "fallback_used": False,
            "retrieved_source_ids": [item["id"] for item in packet["sources"]],
            "sent_sources": sent_sources, **contexts, "discourse_act": act,
            "estimated_prompt_tokens": sum(self._estimate_tokens(m["content"]) + 8 for m in messages),
            "model_input": messages,
        }
        payload = {
            "model": self.model, "messages": messages, "stream": False, "think": False,
            "keep_alive": "30s",
            "options": {"temperature": 0.18, "top_p": 0.9, "repeat_penalty": 1.10,
                        "num_ctx": NPC_CONTEXT_TOKENS, "num_predict": NPC_REPLY_TOKENS},
        }
        try:
            start = perf_counter()
            request = Request(self.url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                              headers={"Content-Type": "application/json"}, method="POST")
            self.last_trace["model_called"] = True
            with urlopen(request, timeout=90) as response:
                result = json.load(response)
            raw = result["message"]["content"]
            reply = clean_reply(raw, finish_reason=result.get('done_reason'))
            self.last_trace["generation"] = {key: result.get(key) for key in
                ("done_reason", "prompt_eval_count", "eval_count")}
            self.last_trace["reply_trimmed"] = reply != str(raw).strip()
            self.last_trace["draft_reply"] = raw
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            self.last_trace.update(route="generation_error", error=str(error), output_origin='system_error')
            return {"success": False, "message": f"NPC 对话生成失败：{error}"}
        print(f"[NpcTalkSkill] 对话生成耗时: {perf_counter() - start:.2f}s")
        try:
            issues = self._draft_issues(reply, packet, sent_sources)
        except (OSError, sqlite3.Error, RuntimeError) as error:
            issues = ['guard_lookup_unavailable']
            self.last_trace['guard_error'] = str(error)
        self.last_trace['guard'] = {
            'status': 'rejected' if issues else 'passed_rules', 'issues': issues,
            'scope': '身份、称呼、设备经历与部分姓名/职位/关系词检查；不是完整语义事实核验',
        }
        self.last_trace['initial_guard'] = dict(self.last_trace['guard'])
        if issues:
            # Retry once using the same evidence. Rejected drafts never become
            # conversation history or a new source of facts.
            repaired_messages = [dict(m) for m in messages]
            repaired_messages[-1] = {'role': 'user', 'content': user_text +
                '\n【重写要求】上一稿未采用。' + self._repair_guidance(issues, packet)}
            repair_payload = {**payload, 'messages': repaired_messages}
            self.last_trace['repair_input'] = repaired_messages
            try:
                request = Request(self.url, data=json.dumps(repair_payload, ensure_ascii=False).encode('utf-8'),
                                  headers={'Content-Type': 'application/json'}, method='POST')
                with urlopen(request, timeout=90) as response:
                    repair_result = json.load(response)
                repair_raw = repair_result['message']['content']
                repaired = clean_reply(repair_raw, finish_reason=repair_result.get('done_reason'))
                repair_issues = self._draft_issues(repaired, packet, sent_sources)
                self.last_trace['repair'] = {'draft_reply': repair_raw, 'issues': repair_issues,
                    'generation': {key: repair_result.get(key) for key in
                                   ('done_reason', 'prompt_eval_count', 'eval_count')}}
                issues = repair_issues
                if not issues:
                    reply = repaired
                    self.last_trace['route'] = 'repaired_dialogue'
            except (OSError, ValueError, KeyError, TypeError, AttributeError, sqlite3.Error, RuntimeError) as error:
                self.last_trace['repair_error'] = str(error)
        if issues:
            reply = '【生成失败】这轮回复未通过检查，未输出角色台词。输入“查看证据”可查看草稿和原因。'
            self.last_trace.update(route='generation_rejected', output_origin='system_error', reply=reply)
            self.last_trace['guard'].update(status='rejected', issues=issues)
            # Preserve the user's referent without turning a rejected draft or
            # diagnostic message into dialogue history or factual evidence.
            self.history.clear()
            self.history_topics.clear()
            self.last_entities = [s['id'] for s in packet['subjects'] if s['id'] != IDENTITY.entity_id]
            self.topic_state = {'question': user_text, 'intent': packet['intent'],
                                'subjects': packet['subjects'], 'sources': sent_sources}
            self.pending_reference = next_pending
            print(f"[NpcTalkSkill] 回复校验拦截: {', '.join(issues)}")
            return {'success': False, 'message': reply}
        self.last_trace['guard'].update(status='passed_rules', issues=[])
        self.last_trace.update(generation_accepted=True, output_origin='model')
        self._remember(user_text, reply, packet, sent_sources)
        self.pending_reference = next_pending
        self.last_trace["reply"] = reply
        return {"success": True, "message": reply}
