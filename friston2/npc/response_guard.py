"""Reply cleaning and bounded checks, not a general-purpose fact checker.

Self questions have narrow grammar so another character's story, a hypothetical
and ordinary character knowledge are still handled by the lore dialogue path.
"""
import re

from friston2.npc.dialogue import conversation_act
from friston2.npc.identity import IDENTITY


def _normalized(text):
    return re.sub(r"\s+", "", str(text or "").lower()).replace("－", "-").replace("—", "-")


def classify_self_question(user_text):
    """Return a self-question kind, a self-history kind, or an empty string.

History kinds distinguish travel/encounters from actual camera task outcomes.
They classify the request only: the caller must consult relevant real records.
"""
    q = _normalized(user_text)
    # Requests to imagine/retell are not assertions that this device was there.
    if re.search(r"如果|假如|假设|要是|倘若|设想|想象|假装|虚构|编(?:个|一个|段|一段)", q):
        return ""
    if re.search(r"(?:讲|说|介绍|复述|转述).{0,20}(?:剧情|故事)", q):
        return ""

    bare = q.strip("，,。.!！?？~～：:")
    if re.fullmatch(r"(?:那|那么)?你(?:最)?(?:喜欢|亲近|偏爱)(?:的)?(?:人(?:是谁|是哪个|呢)?|谁)(?:啊|呀|呢|吗)?", bare):
        return "favorite_person"
    if re.fullmatch(r"(?:那|那么)?你(?:最)?信任(?:的)?(?:人(?:是谁|是哪个|呢)?|谁)(?:啊|呀|呢|吗)?", bare):
        return "trusted_person"
    if re.fullmatch(r"你(?:最)?喜欢什么(?:东西|事情)?(?:啊|呀|呢|吗)?", bare):
        return "preferences"
    if re.fullmatch(r"(?:你(?:现在)?(?:能做什么|会什么|有什么功能)|你的(?:功能|能力)(?:是什么)?)(?:啊|呀|呢|吗)?", bare):
        return "capabilities"
    if re.fullmatch(r"(?:请|麻烦你|能不能|可以)?(?:自我介绍|介绍(?:一下|下)?(?:你)?自己|介绍一下你|你是谁|你叫什么(?:名字)?|你的名字(?:是什么|是啥)?)(?:一下|吗|吧|呢|呀|啊|呗|好吗)?", bare):
        return "identity"

    creator = (
        r"(?:你的|你)(?:制造者|设计者|研发者|研发负责人|维护者|维护负责人|制作者)(?:是谁|叫什么|是哪位|是哪个|呢|$)",
        r"(?:谁|哪位)(?:负责)?(?:制造|设计|研发|制作|维护|造)(?:和维护)?(?:了|的)?你",
        r"你是(?:由)?谁(?:制造|设计|研发|制作|造|维护)",
        r"(?:制造|设计|研发|制作|造)(?:了)?你(?:的)?(?:是(?:谁|哪位)|吗|对吗)",
    )
    if any(re.search(pattern, bare) for pattern in creator):
        return "creator"

    for name, kind in ((r"(?:trevor(?:friston)?|特雷弗(?:·?弗里斯顿)?)", "trevor_relation"),
                       (r"(?:friston-?3|三号机)", "friston3_relation")):
        if not re.search(name, q):
            continue
        self_ref = r"(?:你|friston-?3|三号机|弗里斯顿)"
        paired = (re.search(self_ref + r"(?:和|与|跟|同)(?:原作的|官方的|那个)?" + name, q)
                  or re.search(name + r"(?:这个人|这台机器)?(?:和|与|跟|同)" + self_ref, q))
        relation = re.search(r"关系|区别|联系|关联|一样|同一个|记忆|人格|前代|后代|传承|原型", q)
        identity = (re.search(r"你(?:是|是不是|就是)(?:原作的|官方的|那个)?" + name, q)
                    or re.search(r"你(?:有|有没有|拥有|继承了?)(?:了)?" + name + r"的?(?:记忆|人格|数据)", q)
                    or re.search(name + r"(?:是不是你|是你|的记忆是不是你的|是你的原型)", q))
        if (paired and relation) or identity:
            return kind

    # Require this device (or an omitted conversational subject after a time
    # marker), rather than matching "去过" inside an arbitrary lore question.
    subject = r"(?:你(?:自己)?(?:最近|以前|曾经|之前|上次|刚才|昨天|今天)?(?:有没有|是否|都|曾|还)?|^(?:那|那么)?(?:最近|上次|以前|之前|昨天|今天)(?:你)?(?:有没有|是否))"
    recall_subject = (r"(?:(?:你还记得|你记不记得|你记得|还记得|记不记得)(?:我们|你)?"
                      r"(?:上次|刚才|之前|以前|最近|昨天|今天)?|"
                      r"(?:我们|你)(?:上次|刚才|之前|以前|最近|昨天|今天))")
    encounter = r"(?:遇到过?|遇见过?|见到过?|见过|见了|碰到过?|认识过)"
    travel = r"(?:去过|到过|去哪里了|去了哪|到哪了|旅行过|出过差)"
    if (re.search(subject + encounter + r"(?!的?人物资料)", q)
            or re.search(recall_subject + encounter, q)):
        return "self_encounter"
    if (re.search(subject + travel, q)
            or re.search(recall_subject + r"(?:去过|去了|去哪|去哪里|到过|到了|到哪|出差|旅行)", q)):
        return "self_travel"
    task_subject = r"(?:你|我们)(?:上次|刚才|之前|以前|最近)?(?:有没有|是否|都|还)?"
    task = (r"(?:拍过|拍了|拍摄过|拍摄了|拍照过|拍照了|"
            r"(?:做过|做了|完成过|完成了|执行过|执行了)(?:什么|哪些|任务|工作)|"
            r"(?:执行|完成)(?:过的|的)?任务)")
    if re.search(task_subject + task, q) or re.search(recall_subject + task, q):
        return "self_task_history"
    # An omitted subject is common when checking a just-completed photograph.
    # Require a past-time marker and an outcome question, not a new command.
    photo_outcome = (
        r"(?:那|那么)?(?:刚才|刚刚|上次|之前|最近)(?:的)?(?:那张|这张|那次|这次)?"
        r"(?:照片|拍照|拍摄)(?:的)?(?:拍得怎么样|怎么样|如何|结果(?:是什么|怎么样|如何)?|"
        r"(?:拍好|拍完|成功|失败|完成|拍到|拍下来)(?:了)?(?:吗|没有|没)?)"
        r"[？?呢啊呀。!！]*"
    )
    if re.fullmatch(photo_outcome, q):
        return "self_task_history"
    if (re.search(subject + r"(?:经历过?|经历了|发生过)", q)
            or re.search(r"你的(?:亲身经历|经历|过去|回忆|往事)", q)
            or re.search(recall_subject + r"(?:经历过?|经历了|发生过)", q)):
        return "self_experience"
    return ""


def clean_reply(raw, max_sentences=3, max_chars=None, *, finish_reason=None):
    """Clean presentation without silently discarding a qualification.

    Normal short replies may omit terminal punctuation or use English periods.
    A generation explicitly stopped by its token limit needs regeneration;
    retaining just its opening could turn
    a later-qualified claim into an unqualified one. No grammar check is implied.
    Length and sentence count are prompt preferences; a complete 142-character
    reply is not an empty or truncated reply. The model's token budget still
    bounds generation. An explicit caller limit remains available but never
    silently trims text. max_sentences is retained for call compatibility.
    """
    if (not isinstance(raw, str) or max_sentences <= 0
            or (max_chars is not None and max_chars <= 0)):
        return ""
    if str(finish_reason or '').casefold() in ('length', 'max_tokens', 'max_output_tokens', 'token_limit'):
        return ""
    reply = raw.strip()
    prefix = r"^(?:答(?:复|案)?|回答|Friston(?:[-－ ]?3)?|弗里斯顿|助手|assistant)\s*[:：]\s*"
    reply = re.sub(prefix, "", reply, flags=re.I)
    if not reply or (max_chars is not None and len(reply) > max_chars):
        return ""
    return reply


def _unquoted(text):
    """Quoted dialogue is checked separately for explicit speaker attribution."""
    def keep_label(match):
        inner = match.group()[1:-1]
        return inner if len(inner) <= 12 and not re.search(r'[。！？!?我您]', inner) else ''
    return re.sub(r'“[^”]*”|「[^」]*」|『[^』]*』|"[^"\n]*"', keep_label, text)


_QUALIFIED = re.compile(r'可能|或许|疑似|推测|猜测|未证实|未确认|不确定|尚不明确|存疑|据说|不能确认|无法确认|不能确定')


def _usable_source(source, *, qualified=False):
    """verified=False describes provenance, not absence of source evidence."""
    if source.get('kind') in ('coverage', 'relation_candidate'):
        return False
    if source.get('status') == 'uncertain':
        return qualified
    if source.get('source_type') == 'llm_summary':
        return source.get('status') == 'source_asserted'
    return source.get('verified') is not False


def _support_text(sources):
    return '\n'.join(' '.join((source.get('subject_name', ''), source.get('object_name', ''),
                              source.get('text', ''))) for source in sources)


def _self_id():
    return getattr(IDENTITY, 'entity_id', 'wiki:char_4093_frston')


def _self_names():
    return {IDENTITY.name, getattr(IDENTITY, 'self_name', 'Friston'),
            '弗里斯顿', '特雷弗·弗里斯顿', 'Trevor Friston'}


def _claim_terms(text):
    """Small lexical coverage check; it does not establish semantic entailment."""
    text = _normalized(text)
    for word in sorted(_self_names(), key=len, reverse=True):
        text = text.replace(_normalized(word), '')
    text = re.sub(r'我(?:们|的)?|他(?:们|的)?|她(?:们|的)?|它(?:们|的)?|'
                  r'曾经|以前|当年|其实|仍然|已经|现在|只记得|记得|还记得|那些|这副|一部分', '', text)
    parts = re.findall(r'[\u3400-\u9fff]{2,}|[a-z0-9_]{2,}', text)
    terms = set()
    for part in parts:
        terms.update(part[i:i + 2] for i in range(len(part) - 1))
    return terms - {'一个', '一些', '可以', '但是', '不是', '没有', '时候'}


def _supported_self_memory(sentence, sources):
    """Only an actually sent, self-owned canon passage may support roleplay memory."""
    terms = _claim_terms(sentence)
    if not terms:
        return False
    for source in sources:
        if source.get('subject_id') != _self_id() or not _usable_source(
                source, qualified=bool(_QUALIFIED.search(sentence))):
            continue
        # A warning such as "不能声称去过特里蒙" must never become positive
        # location evidence merely because its words overlap the draft.
        passages = [part for part in re.split(r'[。！？!?；;]', source.get('text', ''))
                    if part and not re.search(r'不能|不可|不代表|不等于|不证明|无从|没有.{0,8}(?:记录|依据)', part)]
        for passage in passages:
            overlap = terms & _claim_terms(passage)
            if len(overlap) >= min(2, len(terms)) and len(overlap) / len(terms) >= 0.55:
                return True
    return False


_SELF_RELATION = re.compile(
    r'(?:我|我们).{0,12}(?:认识|相识|熟识|熟悉|结识|信任|信赖|亲近|并肩|共事|合作)|'
    r'(?:我的|(?:是|作为)我(?:的)?|我(?:最(?:为)?|唯一|特别).{0,6}的|和我|与我|跟我|同我|我们之间).{0,18}'
    r'(?:朋友|好友|挚友|伙伴|同伴|搭档|同事|亲人|家人|恋人|上司|上级|下属|老师|导师|学生|'
    r'父亲|母亲|哥哥|姐姐|弟弟|妹妹|监护人|助手|制造者|设计者|维护者)|'
    r'(?:我|我们)(?:和|与|跟).{1,18}(?:朋友|伙伴|同伴|搭档|同事|共事|并肩|相识)|'
    r'(?:我|我们)(?:就是|是|算是).{0,10}(?:朋友|伙伴|同伴|同事|搭档)|'
    r'(?:认识我|见过我|和我很熟)')

_NONASSERTIVE_RELATION = re.compile(
    r'不能|无法|不敢|还说不清|尚不清楚|不确定|没有依据|没法|不该|不能确认|'
    r'不是.{0,4}(?:朋友|伙伴|同事|搭档)|不愿(?:意)?(?:猜|编)')


def _clauses(sentence):
    return [part.strip() for part in re.split(r'[，,]|但是|不过|可是|而且|但', sentence) if part.strip()]


def _relation_type_supported(sentence, proof):
    """A direct edge proves neither an arbitrary relationship type nor its rank."""
    requirements = (
        (r'认识|相识|熟识|熟悉|结识|认识我|见过我|和我很熟',
         r'认识|相识|熟识|熟悉|结识|见面|见过|交流|交谈|互动|探望|拜访|讨论|共事|合作|设计|制造|改装'),
        (r'朋友|好友|挚友', r'朋友|好友|挚友'),
        (r'伙伴|同伴', r'伙伴|同伴'),
        (r'搭档', r'搭档'), (r'同事|共事', r'同事|共事|同僚'),
        (r'并肩作战', r'并肩作战|共同作战|一同作战|共同战斗|一同战斗'),
        (r'并肩(?!作战)', r'并肩'),
        (r'合作', r'合作'), (r'信任|信赖', r'信任|信赖'), (r'亲近', r'亲近'),
        (r'直属上司|直属上级', r'直属上司|直属上级|直接上级'),
        (r'(?<!直属)上司|(?<!直属)上级', r'上司|上级'),
        (r'下属', r'下属'), (r'导师|老师', r'导师|老师'), (r'学生', r'学生'),
        (r'亲人|家人', r'亲人|家人'), (r'恋人', r'恋人'),
        (r'父亲', r'父亲'), (r'母亲', r'母亲'), (r'哥哥', r'哥哥|兄长'),
        (r'姐姐', r'姐姐'), (r'弟弟', r'弟弟'), (r'妹妹', r'妹妹'),
        (r'监护人', r'监护人'), (r'助手', r'助手'),
        (r'制造者|设计者|维护者', r'制造|设计|维护'),
        (r'多年|数年|很久', r'多年|数年|很久'),
        (r'从小|青梅竹马|一辈子|毕生', r'从小|青梅竹马|一辈子|毕生'),
        (r'最信任|最为信任', r'最信任|最为信任'),
        (r'最喜欢|最为喜欢', r'最喜欢|最为喜欢'),
        (r'最亲近|最为亲近', r'最亲近|最为亲近'),
    )
    for claim, support in requirements:
        if re.search(claim, sentence) and not re.search(support, proof):
            return False
    for match in re.finditer(r'最(?:为)?(?:要好|亲密|重要|可靠)(?:的)?(?:朋友|伙伴|人)?|'
                             r'最好的?(?:朋友|伙伴|搭档)|唯一的?(?:朋友|伙伴|亲人)|'
                             r'(?:数十|几十|[0-9]+)年', sentence):
        claim = match.group(0).replace('最为', '最').replace('的', '')
        if claim not in proof.replace('最为', '最').replace('的', ''):
            return False
    if re.search(r'不认识|并不相识|从未见过|未曾见过', proof) and not re.search(
            r'不认识|并不相识|从未见过|未曾见过', sentence):
        return False
    return True


def _self_relation_supported(sentence, targets, sources):
    if not targets:
        return False
    for target in targets:
        pair = {_self_id(), target['id']}
        direct = [source for source in sources if source.get('kind') == 'relation'
                  and {source.get('subject_id'), source.get('object_id')} == pair
                  and _usable_source(source, qualified=bool(_QUALIFIED.search(sentence)))]
        if not direct or not _relation_type_supported(sentence, _support_text(direct)):
            return False
    return True


def _elliptical_acquaintance(clause, subjects):
    """In a self-pair question, a bare '当然认识' still asserts familiarity."""
    if not any(subject['id'] == _self_id() for subject in subjects):
        return False
    targets = [subject['name'] for subject in subjects if subject['id'] != _self_id()]
    target = '|'.join(re.escape(name) for name in targets)
    referent = r'(?:他|她|它' + ('|' + target if target else '') + r')?'
    return bool(re.fullmatch(r'(?:当然|自然|是的|嗯)?(?:认识|熟悉|熟识)' + referent + r'(?:得很|很久)?', clause))


def _name_only_utterance(sentence, name):
    """Narrow metalinguistic questions/denials, not a blanket negative bypass.

    Match the entire sentence so '我不把你称作博士，他是我的朋友' still
    needs evidence. Negating a real relationship is itself a factual claim.
    """
    value = re.sub(r'[“”「」『』\"]', '', sentence).strip()
    name = re.escape(name)
    patterns = (
        r'(?:我)?(?:不会|不|没有|并未|不能|不该|不应)(?:把|将)?你(?:称作|称为|叫作|叫做|认作|当成|称呼为)' + name,
        r'(?:我)?(?:不会|不|不能|不该)(?:默认|假定|推定)你是' + name,
        r'(?:你说的|你提到的|你问的)(?:是)?' + name + r'(?:这个名字|这个称呼)?(?:是指谁|指的是谁|是什么意思|是谁|吗)',
        r'(?:我)?(?:还|暂时)?(?:不知道|不清楚|不能确认|无法确认)' + name + r'(?:是谁|指的是谁|这个名字指谁)',
    )
    return any(re.fullmatch(pattern, value) for pattern in patterns)


def _doctor_address(reply):
    """Catch common vocatives, including '你好，博士', not all name mentions."""
    return bool(re.search(
        r'(?:^|[。！？!?])\s*(?:博士|Doctor)[，,！!：:]|'
        r'(?:你好|您好|早安|早上好|晚上好|晚安|谢谢你?|hello|hi)[，,\s]*(?:博士|Doctor)(?=[。.!！?？\s]|$)',
        reply, re.I))


def _candidate_question(sentence, candidate_entities, user_text=''):
    """A choice between candidate names asserts neither identity nor biography."""
    names = {item.get('name', '') for item in candidate_entities if item.get('id') and item.get('name')}
    if not names:
        return False
    value = re.sub(r'''[“”「」『』\"‘’']''', '', sentence).strip()
    value = re.sub(r'[？?]$', '', value).strip()
    # A comma can join the choice to a separate request for clarification.
    # Strip only a full, nonfactual meta-clause; never arbitrary trailing prose.
    meta_words = (r'(?:请|你|您|明确|确认|指明|指定|说明|一下|具体|'
                  r'指的|所指|指向|指代|是|哪一位|哪位|哪一个|哪个|谁|呢|吗|其中|的)+')
    parts = re.split(r'[，,]', value)
    if (len(parts) > 1 and re.fullmatch(meta_words, parts[-1].strip())
            and re.search(r'明确|确认|指明|指定|说明', parts[-1])):
        value = ','.join(parts[:-1]).strip()
    # Courtesy + requester can compose ("请问您想确认的..."). Removing
    # courtesy here changes no proposition; the full choice still must match.
    value = re.sub(r'^请问(?:一下)?\s*[，,]?\s*(?=[你您])', '', value)
    alternatives = '(?:' + '|'.join(re.escape(name) for name in sorted(names, key=len, reverse=True)) + ')'
    # A bare candidate list asserts no identity, role or biography. The caller
    # separately requires a clarification question somewhere in the reply.
    candidate_list = alternatives + r'(?:\s*(?:、|和|与|或)\s*' + alternatives + r')+'
    if re.fullmatch(candidate_list, value):
        return True
    among = re.fullmatch(candidate_list + r'(?:中|之中)[，,]?\s*([^，,。！？!?]{0,12}?)'
                         r'(?:指的是|指的|指向的|是)?哪(?:一)?(?:位|个)(?:呢|吗)?', value)
    if among and (not among.group(1) or among.group(1) in user_text):
        return True
    pause = r'\s*[，,]?\s*'
    requester = r'(?:(?:你|您)' + pause + r')?'
    desire = r'(?:想(?:要)?|要|希望)?'
    inquiry = r'(?:询问|问|确认|了解|称呼)'
    reference = r'(?:是指|指的是|说的是|问的是|指|说的|是)'
    prefix = (requester + r'(?:' + reference + r'|(?:是)?' + desire
              + inquiry + r'(?:的是)?)' + pause)
    choice = alternatives + '(?:' + pause + '还是\\s*' + alternatives + r')+(?:呢|吗)?'
    # The alternatives themselves form a question ("A还是B"). Optional
    # requester/inquiry phrases add no facts; arbitrary prose is never a prefix.
    if (re.fullmatch(r'(?:' + prefix + r')?' + choice, value)
            or re.fullmatch(prefix + alternatives + r'吗', value)):
        return True
    # Only repeat an actual user phrase, not an invented proposition disguised
    # as "你说的...". Candidate names alone cannot license that attribution.
    topic_head = requester + r'(?:是)?(?:所?说|(?:所)?提到|口中|' + desire + inquiry + r')的?'
    topic = re.fullmatch(
        r'(?:(?:' + topic_head + r'|请问)' + pause + r')?'
        + r'([^，,。！？!?]{1,12}?)' + pause + r'(?:是指|指的是|指|是)' + pause + choice, value)
    return bool(topic and topic.group(1) in user_text)


def _stage_action(clause):
    """Narrow physical narration check, not a general metaphor/style classifier.

    Only direct first-person gestures count. Mental/social phrases such as
    "我想", "我认为" and "我听着" do not imply an executed physical action.
    """
    return bool(re.match(
        r'\s*我(?:轻轻|微微|缓缓|默默|慢慢|随即)?(?:地)?(?:'
        r'颔首|点了点头|摇了摇头|点头|摇头|'
        r'(?:拍了拍|拍着)(?:你的肩(?:膀)?|(?:身旁的|面前的)?(?:工作台|桌子|桌面)))', clause))


def _whole_quoted_text(reply):
    """Recognize an outer presentation quote without stripping other quotes."""
    value = reply.strip()
    pairs = {'“': '”', '「': '」', '『': '』', '"': '"'}
    if len(value) >= 2 and value[0] in pairs and value[-1] == pairs[value[0]]:
        return value[1:-1].strip()
    return None


def _social_scolding(reply, act):
    """Only obvious unwarranted scolding in social openings; not a style judge."""
    if act not in ('greeting', 'thanks', 'compliment'):
        return False
    return bool(re.search(
        r'(?:别再|不要再|不许|不准).{0,14}(?:跟我|对我|和我).{0,6}(?:说话|讲话)|'
        r'(?:问候|开场白|标点|语气).{0,14}(?:轻飘|随意|敷衍|规矩|像样)|'
        r'连.{0,12}(?:标点|规矩|礼貌|参数).{0,10}(?:不懂|不会|不记得|记不清|都省|都没)|'
        r'我们之间.{0,8}规矩|你(?:真是|就是|简直).{0,5}(?:愚蠢|无知|蠢|笨)',
        reply))


def validate_reply(reply, subjects, sources, mentioned_entities=(), *, user_text='', act='', user_role='', candidate_entities=()):
    """Conservative, lightweight rules; NOT a semantic fact-proof engine.

    Check speaker, device actions, named entities and surface role/relationship
    claims against the sources actually sent (not the full retrieval packet).
    Passing these rules does not certify all paraphrases or story details. The
    optional social-style check is a narrow heuristic, not a semantic evaluator.
    user_role must come from explicit dialogue state, never model-generated text.
    """
    issues = []
    if not reply:
        return ["empty_or_incomplete_reply"]
    outer_quote = _whole_quoted_text(reply)
    quoted_choice = (outer_quote is not None
                     and _candidate_question(outer_quote, candidate_entities, user_text))
    # Only a complete candidate-choice question may use quotation marks as
    # presentation. Keep it visible to the usual identity/relationship checks.
    text = outer_quote if quoted_choice else _unquoted(reply)
    if (outer_quote is not None and not quoted_choice
            and any(item.get('name') and item['name'] in outer_quote for item in candidate_entities)):
        issues.append('unattributed_character_quote')
    usable = [s for s in sources if _usable_source(s)]
    uncertain = [s for s in sources if s.get('status') == 'uncertain']
    self_id = _self_id()
    entities = {s['id']: s for s in subjects if s.get('id') and s.get('name')}
    entities.update({s['id']: s for s in mentioned_entities if s.get('id') and s.get('name')})
    for source in sources:
        for prefix in ('subject', 'object'):
            if source.get(prefix + '_id') and source.get(prefix + '_name'):
                entity_id = source[prefix + '_id']
                entities.setdefault(entity_id, {'id': entity_id, 'name': source[prefix + '_name']})
    others = [entity for entity in entities.values() if entity['id'] != self_id]
    primary = [s for s in subjects if s['id'] != self_id]
    names = {s['name'] for s in subjects if s.get('name')}
    names.update(s.get('subject_name', '') for s in usable)
    names.update(s.get('object_name', '') for s in sources)
    names.update(entity['name'] for entity in mentioned_entities)
    names.add(IDENTITY.creator)
    names.discard('')
    names.difference_update(_self_names())
    act = act or conversation_act(user_text)
    doctor_role = str(user_role).casefold() in ('doctor', '博士')
    if _doctor_address(text) and not doctor_role:
        issues.append('unassigned_user_role:doctor')
    if _social_scolding(text, act):
        issues.append('inappropriate_social_scolding')
    if names and re.search(r'(?:^|[。！？!?])\s*(?:' + '|'.join(map(re.escape, names)) + r')\s*[:：]', text):
        issues.append('foreign_speaker_label')
    if any(re.search(r'我(?:就|正)?是\s*' + re.escape(name), text) for name in names):
        issues.append('foreign_self_identity')
    if re.search(r'我(?:就|正)?是(?:罗德岛的?)?(?:公开领袖|领袖|总工程师|采购部负责人|博士)', text):
        issues.append('foreign_self_role')
    # Completed physical actions remain exclusive to the execution-record path.
    # A lore passage mentioning a camera or a robot arm cannot prove this turn.
    antecedents = primary if len(primary) == 1 else []
    for sentence in re.split(r'[。！？!?；;]', text):
        if (re.search(r'(?:我|我们).{0,10}(?:未|没有|从未).{0,10}(?:往来|相识|见过|认识|交集)', sentence)
                and not re.search(r'证据|依据|不能确认|无法确认|不确定', sentence)
                and not _supported_self_memory(sentence, sources)):
            issues.append('unsupported_negative_acquaintance')
        if (not primary and any(s['id'] == self_id for s in subjects)
                and re.match(r'\s*(?:去过|到过|见过|曾经|以前)', sentence)
                and not _supported_self_memory('我' + sentence, sources)):
            issues.append('unsupported_self_biography')
        for clause in _clauses(sentence):
            nonassertive = bool(_NONASSERTIVE_RELATION.search(clause))
            stage_action = _stage_action(clause)
            if stage_action:
                issues.append('unsupported_stage_action')
            elif re.search(r'(?:我|我们).{0,8}(?:拍好了|拍完了|拍过|拍了|递给了|抓住了|拿给你|送到了)', clause):
                if not nonassertive:
                    issues.append('unsupported_device_experience')
            if re.search(r'(?:我|我们)(?:曾经|以前|当年|小时候|出生|加入了|在.{0,18}(?:长大|研究了|造了)|'
                         r'(?:还|只)?记得|.{0,5}(?:去过|到过|经历过|见过|遇见过))', clause):
                if not nonassertive and not _supported_self_memory(clause, sources):
                    issues.append('unsupported_self_biography')
            explicit = [entity for entity in others if entity['name'] in clause]
            if explicit:
                antecedents = explicit
            if (_SELF_RELATION.search(clause) or _elliptical_acquaintance(clause, subjects)) and not _NONASSERTIVE_RELATION.search(clause):
                targets = explicit or antecedents
                if not _self_relation_supported(clause, targets, sources):
                    issues.append('unsupported_personal_acquaintance')
    for sentence in re.split(r'[。！？!?；;]', text):
        if re.search(r'(?:研发|维护|制造|设计).{0,8}我|我的(?:研发|维护|制造|设计)', sentence):
            actors = [(sentence.find(name), name) for name in names if name in sentence]
            actor = min(actors)[1] if actors else next(
                (s['name'] for s in subjects if s['id'] != self_id), '')
            if actor and actor != IDENTITY.creator:
                issues.append('wrong_creator_or_self_relationship')
    if re.search(r'我.{0,10}(?:负责|主持).{0,8}(?:研发|制造|维护).{0,6}(?:终端|Friston-3)', text, re.I):
        issues.append('reversed_creator_relationship')

    # Bind role words to the nearest named subject (or a single clear referent),
    # so A's role somewhere in the packet cannot automatically support B's role.
    role_terms = ('总工程师', '采购部负责人', '主任', '领袖', '主管', '战术指挥官',
                  '父亲', '母亲', '哥哥', '姐姐', '妹妹', '弟弟', '恋人', '搭档',
                  '朋友', '导师', '监护人', '学生', '同事', '亲属',
                  '萨卡兹', '卡特斯', '黎博利', '沃尔珀', '卡兹戴尔', '莱茵生命', '小兔子')
    target = primary[0] if len(primary) == 1 else None
    for sentence in re.split(r'[。！？!?；;]', text):
        if not sentence.strip():
            continue
        qualified = bool(_QUALIFIED.search(sentence))
        sentence_sources = [s for s in sources if _usable_source(s, qualified=qualified)]
        support = _support_text(sentence_sources)
        if uncertain and not usable and not qualified:
            issues.append('unqualified_uncertain_evidence')
        # An uncertain sentence's qualification must not license a separate,
        # unqualified assertion elsewhere in the reply.
        for entity in mentioned_entities:
            if entity['id'] == self_id or entity['name'] not in sentence:
                continue
            if _name_only_utterance(sentence, entity['name']):
                continue
            if (any(item.get('id') == entity['id'] for item in candidate_entities)
                    and _candidate_question(sentence, candidate_entities, user_text)):
                continue
            if doctor_role and entity['name'] == '博士' and _doctor_address(sentence):
                continue
            if (entity['name'] not in support and not (
                    any(subject['id'] == entity['id'] for subject in subjects)
                    and _NONASSERTIVE_RELATION.search(sentence))):
                issues.append('unsupported_named_entity:' + entity['name'])
        for clause in _clauses(sentence):
            hits = [(clause.rfind(s['name']), s) for s in subjects if s['name'] in clause]
            if hits:
                target = max(hits, key=lambda pair: pair[0])[1]
            elif re.match(r'我(?:是|的职业是|担任)', clause):
                target = {'id': self_id}
            owned = '\n'.join(s.get('text', '') for s in sentence_sources
                              if target and (s.get('subject_id') == target['id']
                                             or s.get('object_id') == target['id']))
            for term in role_terms:
                if term in clause and term not in owned and not _NONASSERTIVE_RELATION.search(clause):
                    issues.append('unsupported_role_or_relation:' + term)
    # Unattributed first-person quotation must not become the NPC's own voice.
    if not quoted_choice and re.search(r'[“「『"][^”」』"]*(?:我|您)[^”」』"]*[”」』"]', reply):
        quotes = re.findall(r'[“「『"]([^”」』"]*(?:我|您)[^”」』"]*)[”」』"]', reply)
        own_quote = bool(quotes) and all(_supported_self_memory(quote, sources) for quote in quotes)
        if not own_quote and not any(name in text for name in names):
            issues.append('unattributed_character_quote')
    return list(dict.fromkeys(issues))


