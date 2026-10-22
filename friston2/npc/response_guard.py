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

    if (re.search(r'你(?:的)?(?:人格|意识|记忆|数据)', q)
            and re.search(r'创造|制造|设计|来源|来自|提供|赋予|谁', q)):
        return "personality_origin"

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
    r'(?<!自)(?:(?:我|我们).{0,12}(?:认识|相识|熟识|熟悉|结识|信任|信赖|亲近|并肩|共事|合作)|'
    r'(?:我的|(?:是|作为)我(?:的)?|我(?:最(?:为)?|唯一|特别).{0,6}的|和我|与我|跟我|同我|我们之间).{0,18}'
    r'(?:朋友|好友|挚友|熟人|伙伴|同伴|搭档|同事|亲人|家人|恋人|上司|上级|下属|老师|导师|学生|'
    r'父亲|母亲|哥哥|姐姐|弟弟|妹妹|监护人|雇主|护卫|助手|制造者|设计者|维护者)|'
    r'(?:我|我们)(?:既|也|还)?(?:是|担任|作为).{0,12}'
    r'(?:导师|老师|监护人|雇主|护卫|朋友|熟人|伙伴|同事|搭档)|'
    r'(?:我|我们)(?:和|与|跟).{1,18}(?:朋友|伙伴|同伴|搭档|同事|共事|并肩|相识)|'
    r'(?:我|我们)(?:就是|是|算是).{0,10}(?:朋友|伙伴|同伴|同事|搭档)|'
    r'(?:认识我|见过我|和我很熟))')

_NONASSERTIVE_RELATION = re.compile(
    r'不能|无法|不敢|还说不清|尚不清楚|不确定|没有依据|没法|不该|不能确认|'
    r'不是.{0,4}(?:朋友|伙伴|同事|搭档)|不愿(?:意)?(?:猜|编)')


def _clauses(sentence):
    # A single character is not a reliable conjunction boundary: it can be
    # part of a database entity name. Keep the full name available to matching.
    return [part.strip() for part in re.split(r'[，,]|但是|不过|可是|而且', sentence) if part.strip()]


def _relation_type_supported(sentence, proof):
    """A direct edge proves neither an arbitrary relationship type nor its rank."""
    requirements = (
        (r'认识|相识|熟识|熟悉|熟人|结识|认识我|见过我|和我很熟',
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
        (r'监护人', r'监护人'), (r'雇主', r'雇主|雇佣'), (r'护卫', r'护卫'),
        (r'助手', r'助手'),
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


def _documentary_familiarity(clause):
    """Knowing a dossier is not an assertion of meeting its subject."""
    archival = re.search(
        r'我(?:在|从|通过).{0,8}(?:资料|档案|记录|文献)(?:中|里|上)?'
        r'(?:所)?(?:熟悉|了解|认识|知道)|'
        r'我(?:所)?(?:熟悉|了解)(?:[^，,。！？!?]{1,12}的)?(?:资料|档案|记录|文献)', clause)
    if not archival:
        return False
    remainder = clause[:archival.start()] + clause[archival.end():]
    if _SELF_RELATION.search(remainder):
        return False
    return not re.search(
        r'私下|亲自|现实|见面|会面|见过|相识|结识|共事|并肩|往来|交情|私交|'
        r'一起生活|相处|多年|很久|[一二三四五六七八九十\d]+年', clause)


def _factual_voice(clause):
    """Remove epistemic first-person framing, retaining any self-fact after it."""
    return re.sub(
        r'在我看来|依我看|据我(?:所)?(?:知|了解)|'
        r'我(?:个人|也|更|倒|仍)?(?:认为|觉得|看来|知道|了解|读到|看到|听说|'
        r'欣赏|敬佩|赞同|认可|喜欢|希望|愿意|想|建议)|'
        r'我(?:在|从|通过).{0,8}(?:资料|档案|记录|文献)(?:中|里|上)?'
        r'(?:所)?(?:读到|看到|得知|了解到|知道)', '', clause)


def _borrowed_self_fact(clause, sources):
    """Source wording about another actor cannot become the speaker's history."""
    # “自我价值/自我认识” describes the named actor, not the speaker.
    # Match the pronoun itself; a real “我 ... 自我” still needs self evidence.
    if (not re.search(r'(?<!自)我', clause) or _documentary_familiarity(clause)
            or _NONASSERTIVE_RELATION.search(clause) or re.search(r'如果|假如|假设|设想|要是', clause)
            or _supported_self_memory(clause, sources)):
        return False
    terms = _claim_terms(clause)
    if len(terms) < 3:
        return False
    for source in sources:
        if _self_id() in (source.get('subject_id'), source.get('object_id')) or not _usable_source(source, qualified=True):
            continue
        overlap = terms & _claim_terms(source.get('text', ''))
        if len(overlap) >= 3 and len(overlap) / len(terms) >= 0.5:
            return True
    return False


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
    value = re.sub(r'[。！？!?]+$', '', value).strip()
    # A comma can join the choice to a separate request for clarification.
    # Strip only a full, nonfactual meta-clause; never arbitrary trailing prose.
    meta_words = (r'(?:请|你|您|能否|能不能|可以|能|麻烦|明确|确认|指明|指定|指认|说明|一下|具体|'
                  r'指的|所指|指向|指代|是|哪一位|哪位|哪一个|哪个|一位|一个|谁|呢|吗|其中|的)+')
    parts = re.split(r'[，,]', value)
    if (len(parts) > 1 and re.fullmatch(meta_words, parts[-1].strip())
            and re.search(r'明确|确认|指明|指定|指认|说明', parts[-1])):
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
    requester = r'(?:(?:你|您)(?:这里|这次|刚才|刚刚|现在)?' + pause + r')?'
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
    topic_head = requester + r'(?:是)?(?:所?说|所?指|(?:所)?提到|口中|' + desire + inquiry + r')的?'
    topic = re.fullmatch(
        r'(?:(?:' + topic_head + r'|请问)' + pause + r')?'
        + r'([^，,。！？!?]{1,12}?)' + pause + r'(?:究竟|到底)?(?:是指|指的是|指|是)' + pause + choice, value)
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


def _invented_body_state(reply, user_text):
    """Catch direct bodily claims, including an omitted subject in a greeting.

    This is deliberately not a ban on food/sleep topics: advice, questions to
    the visitor, denied needs, metaphors and hypothetical talk remain valid.
    """
    actual = (r'(?:吃(?:过|了)(?!亏|苦|惊|教训)|吃(?:饱|完)|喝(?:过|了|完)|睡(?:过|了|醒|得)|'
              r'(?:还|尚)?没(?:有)?(?:吃|喝|睡)|刚(?:刚)?醒|失眠|'
              r'(?:饿|渴|困)(?:了|得|坏)|肚子(?:有点|很|真|太)?饿)')
    nonactual = r'如果|假如|假设|要是|倘若|想象|假装|不需要|无需|不用|不会|不能|并不|不觉得|不感到'
    # An immediate answer often drops "我": "午饭还没吃" / "昨晚没睡".
    # Limit this to the reply's opening, before another named person's story
    # can establish a different subject. Questions/advice are not self-reports.
    opening_match = re.match(r'\s*([^，,。！？!?；;]+)([，,。！？!?；;]?)', reply)
    if opening_match:
        opening, ending = opening_match.groups()
        time_or_meal = (r'(?:今天|今早|今晚|昨晚|昨天|昨夜|现在|这会儿|刚才|刚刚|'
                        r'早上|上午|中午|下午|晚上|早饭|午饭|晚饭|早餐|午餐|晚餐)')
        if (ending not in ('?', '？') and not re.search(r'(?:吗|么|吧)$', opening)
                and not re.search(nonactual, opening)
                and not re.search(r'你|您|大家|他|她|它|的是|的那|的人|的角色|要记得|别忘了', opening)
                and re.match(r'^' + time_or_meal + r'(?:倒是|其实|确实|已经|还|也|都|就)?' + actual, opening)):
            return True
    for sentence in re.split(r'[。！？!?；;]', reply):
        if re.search(r'如果|假如|假设|要是|倘若|想象|假装', sentence):
            continue
        for clause in re.split(r'[，,]|但是|不过|可是|而且', sentence):
            if re.search(nonactual, clause) or re.search(r'你|您|大家', clause):
                continue
            if re.search(r'(?:^|\s)我(?:自己)?[^，,。！？!?；;]{0,12}' + actual, clause):
                return True
            if re.search(r'^\s*我(?:现在|今天|刚才)?(?:也|还|已经)?(?:有点|很|真|太)?(?:饿|渴|困)\s*$', clause):
                return True
    # "还没呢" is a bodily claim only when answering a bodily question about
    # the speaker, not after a visitor's report or a question about a task.
    question = _normalized(user_text)
    bodily_question = re.match(
        r'^(?:那(?:么)?)?(?:你(?:今天|刚才|昨晚|现在|早上|中午|晚上)?'
        r'(?:有没有|是不是|是否|已经|还|都|刚)?|(?:早饭|午饭|晚饭|早餐|午餐|晚餐)?)'
        r'(?:吃|喝|睡|饿|渴|困|醒)', question)
    if not bodily_question:
        return False
    opening = re.split(r'[，,。！？!?；;]', reply.strip(), maxsplit=1)[0]
    opening = re.sub(r'^(?:嗯|啊|哈哈)[，,\s]*', '', opening)
    return bool(re.fullmatch(
        r'(?:还没(?:有)?|没(?:有)?|吃过|吃了|吃饱了|喝过|喝了|睡过|睡了|睡醒了|醒了|饱了|饿了|渴了|困了)'
        r'(?:呢|了|呀|啊|哦)?', opening))


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


def _number_value(value):
    """Compare Arabic and common Chinese quantities without model inference."""
    digits = dict(zip('零〇一二两三四五六七八九', (0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9)))
    if not value:
        return 0
    for unit, scale in (('亿', 100000000), ('万', 10000)):
        if unit in value:
            left, right = value.split(unit, 1)
            return (_number_value(left) or 1) * scale + _number_value(right)
    if re.fullmatch(r'\d+(?:\.\d+)?', value):
        return float(value)
    if all(char in digits for char in value):
        return int(''.join(str(digits[char]) for char in value))
    total, number = 0, 0
    for char in value:
        if char in digits:
            number = digits[char]
        else:
            total += (number or 1) * {'十': 10, '百': 100, '千': 1000}[char]
            number = 0
    return total + number


def _quantities(text, entity_names=()):
    """Extract factual counts/dates, excluding names and discourse numbering."""
    for name in sorted(set(entity_names), key=len, reverse=True):
        if name:
            text = text.replace(name, ' ')
    # Model identifiers and alphanumeric names are not quantities. Registered
    # Chinese aliases were removed above using the database's own names.
    text = re.sub(r'[A-Za-z][A-Za-z0-9_.-]*\d[A-Za-z0-9_.-]*', ' ', text)
    text = re.sub(r'(?m)^\s*\d+[、.)]\s*', '', text)
    text = re.sub(r'(?<=\d),(?=\d{3}(?:\D|$))', '', text)
    pattern = (r'\d+(?:\.\d+)?[万亿]?|'
               r'[零〇一二两三四五六七八九十百千万亿]+'
               r'(?=[多余来]?(?:个|位|名|年|岁|天|日|月|小时|分钟|秒|届|次|支|组|家|台|人|枚|米|吨|种|倍|%|％))')
    values = set()
    for match in re.finditer(pattern, text):
        value = _number_value(match.group())
        suffix = text[match.end():]
        prefix = text[max(0, match.start() - 5):match.start()]
        # "一位科学家" introduces a person; it does not assert a population.
        if (value == 1 and re.match(r'[个位名种台家]', suffix)
                and not re.search(r'共|只有|仅有|仅|至少|至多|超过|不足|不到|有$', prefix)):
            continue
        values.add(value)
    return values


def _unsupported_quantity(reply, sources, entities, subjects=(), user_text=''):
    if not sources:
        return False  # Numerical advice in ordinary chat is not a lore claim.
    entities = list(entities)
    names = [entity.get('name', '') for entity in entities]
    names += list(_self_names())
    # Remove only the participant-count phrase; another occurrence of the
    # same number (for example a duration) still needs its own source number.
    participants = subjects or entities
    participant_count = len({entity.get('id') for entity in participants if entity.get('id')})
    # A missing name in the DB does not erase a participant explicitly named
    # by the user. This licenses only "two people" in an uncertainty statement,
    # never user-supplied dates, durations or populations as factual evidence.
    if (participant_count < 2 and _NONASSERTIVE_RELATION.search(reply)
            and re.search(r'[^，,。！？!?、]{1,18}[和与跟][^，,。！？!?、]{1,18}', user_text)):
        reply = re.sub(r'[两二2]人(?!格)', '双方', reply)
    reply = re.sub(r'([两二三四五六七八九十]|[2-9])人(?!格)',
                   lambda match: '双方' if _number_value(match.group(1)) == participant_count
                   else match.group(), reply)
    claimed = _quantities(reply, names)
    if not claimed:
        return False
    proof = '\n'.join(source.get('text', '') for source in sources
                      if _usable_source(source, qualified=True))
    supported = _quantities(proof, names)
    return bool(claimed - supported)


def _unsupported_pair_denial(reply, user_text, subjects, sources):
    """Missing records cannot support a categorical bare 'no' to a pair query."""
    pair = {subject['id'] for subject in subjects if subject.get('id')}
    if len(pair) != 2 or not re.search(r'是不是|是否|有没有|[吗么][？?。!！]*$', user_text):
        return False
    if not re.match(r'\s*(?:不是|并不是|不对|并非如此|没有(?:关系)?)[。！!，,]', reply):
        return False
    names = [subject.get('name', '') for subject in subjects]
    for source in sources:
        if not _usable_source(source):
            continue
        body = source.get('text', '')
        owners = {source.get('subject_id'), source.get('object_id')}
        if not pair <= owners and not all(name and name in body for name in names):
            continue
        for clause in re.split(r'[。！？!?；;，,]', body):
            if re.search(r'(?:没有|无|未|缺少|缺乏).{0,16}(?:记载|资料|信息|记录|证据|依据)', clause):
                continue
            if re.search(r'不是|并非|没有|并没有|从未|未曾|不存在|不属于', clause):
                return False
    return True


def _contradicted_personality_origin(reply, sources, entities=(), user_text=''):
    """Keep an explicit 'did not create my personality' source negative.

    A body's designer is not thereby the supplier of its personality. Only
    self-owned evidence with an explicit denial activates this narrow check.
    Pronouns follow the most recent named person, never a fixed character.
    """
    excluded = set()
    personality = r'人格(?:数据|程式|程序)?'
    for source in sources:
        name = source.get('object_name', '')
        if (not name or source.get('subject_id') != _self_id()
                or not _usable_source(source)):
            continue
        actor = '(?:' + re.escape(name) + r'|她|他|对方)'
        if re.search(personality + r'(?:并)?(?:不是|并非)(?:由)?' + actor
                     + r'(?:所)?(?:创造|制造|设计|研发)', source.get('text', '')):
            excluded.add(name)
    if not excluded:
        return False
    names = excluded | {entity.get('name', '') for entity in entities
                        if entity.get('id') != _self_id() and entity.get('name')}
    actor = '(?:' + '|'.join(re.escape(name) for name in sorted(names, key=len, reverse=True)) + r'|她|他|对方)'
    action = r'(?:创造|制造|设计|研发|生成|赋予|提供)'
    patterns = (
        r'(?P<actor>' + actor + r').{0,16}' + action + r'(?:了)?(?:我(?:的)?)?' + personality,
        personality + r'.{0,8}(?P<actor>' + actor + r')(?:所)?' + action,
        action + r'(?:了)?(?:我(?:的)?)?' + personality + r'(?:的)?(?:是|来自)(?P<actor>' + actor + r')',
        personality + r'(?:是)?(?:来自|出自|源自)(?P<actor>' + actor + r')',
    )
    explicit = sorted((user_text.rfind(name), name) for name in names if name in user_text)
    antecedent = explicit[-1][1] if explicit else next(iter(names)) if len(names) == 1 else ''
    for clause in re.split(r'[，,。！？!?；;]|不过|但是|但|却|而是', reply):
        named = sorted((clause.find(name), name) for name in names if name in clause)
        if named:
            antecedent = named[-1][1]
        if re.search(r'不是|并非|没有|未曾|不能|无法|不确定|不清楚|不负责|不曾|不由|不等于|不代表', clause):
            continue
        for pattern in patterns:
            match = re.search(pattern, clause)
            if match and (match['actor'] if match['actor'] in names else antecedent) in excluded:
                return True
    return False


def validate_reply(reply, subjects, sources, mentioned_entities=(), *, user_text='', act='', user_role='', candidate_entities=(), source_excerpt=False):
    """Conservative, lightweight rules; NOT a semantic fact-proof engine.

    Check source ownership, speaker identity and device actions locally.
    source_excerpt skips lexical checks only for verbatim database excerpts;
    it must not be interpreted as a factuality verdict. Passing these rules does
    not certify arbitrary paraphrases, relationship labels or story details.
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
    # Ambiguous-reference turns authorize only a complete choice question.
    # Checking individual sentences would let an otherwise valid question hide
    # an extra factual assertion behind a pronoun in the following sentence.
    clarification = outer_quote if outer_quote is not None else reply
    if candidate_entities and not _candidate_question(clarification, candidate_entities, user_text):
        issues.append('non_clarification_content')
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
    if not source_excerpt:
        if _unsupported_quantity(text, sources, entities.values(), subjects, user_text):
            issues.append('unsupported_quantity')
        if _unsupported_pair_denial(text, user_text, subjects, sources):
            issues.append('unsupported_negative_relation')
        if _contradicted_personality_origin(text, sources, entities.values(), user_text):
            issues.append('contradicted_personality_origin')
    act = act or conversation_act(user_text)
    doctor_role = str(user_role).casefold() in ('doctor', '博士')
    if _doctor_address(text) and not doctor_role:
        issues.append('unassigned_user_role:doctor')
    if _social_scolding(text, act):
        issues.append('inappropriate_social_scolding')
    if _invented_body_state(text, user_text):
        issues.append('unsupported_body_state')
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
            factual = _factual_voice(clause)
            nonassertive = bool(_NONASSERTIVE_RELATION.search(clause))
            if (re.search(r'(?:我(?:可以|能|来|会|帮你)|需要我|要我).{0,6}(?:查|找|搜).{0,12}'
                          r'(?:附近|现场|地图|天气|休息区|餐厅|摊位|票价|人数)', clause)
                    and not re.search(r'不能|无法|没法|不支持|未接入', clause)):
                issues.append('unsupported_live_service')
            stage_action = _stage_action(clause)
            if stage_action:
                issues.append('unsupported_stage_action')
            elif re.search(r'(?:我|我们).{0,8}(?:拍好了|拍完了|拍过|拍了|递给了|抓住了|拿给你|送到了)', clause):
                if not nonassertive:
                    issues.append('unsupported_device_experience')
            if re.search(r'(?:我|我们)(?:曾经|以前|当年|小时候|出生|加入了|在.{0,18}(?:长大|研究了|造了)|'
                         r'(?:还|只)?记得|.{0,5}(?:去过|到过|经历过|见过|遇见过))', factual):
                if not nonassertive and not _supported_self_memory(factual, sources):
                    issues.append('unsupported_self_biography')
            if not source_excerpt and _borrowed_self_fact(factual, sources):
                issues.append('borrowed_character_experience')
            explicit = [entity for entity in others if entity['name'] in clause]
            if explicit:
                antecedents = explicit
            if (not source_excerpt
                    and (_SELF_RELATION.search(factual) or _elliptical_acquaintance(factual, subjects))
                    and not _NONASSERTIVE_RELATION.search(clause)
                    and not _documentary_familiarity(clause)
                    and not re.search(r'(?:知道|了解|听说过|认识)(?:这个|这一|这个人的)名字', clause)):
                targets = explicit or antecedents
                if not _self_relation_supported(factual, targets, sources):
                    issues.append('unsupported_personal_acquaintance')
    for sentence in re.split(r'[。！？!?；;]', text):
        # A self-introduction such as '作为Friston-3的负责人' also reverses
        # the canon designer / machine relationship, even without '我研发'.
        if (re.search(r'我.{0,10}(?:负责|主持).{0,8}(?:研发|制造|维护).{0,6}(?:终端|Friston-3)', text, re.I)
                or re.search(
                    r'(?:作为|身为|我是)\s*(?:这台)?\s*Friston[-－ ]?3\s*(?:机体)?\s*的?\s*(?:研发)?(?:负责人|设计者|制造者)',
                    text, re.I)):
            actors = [(sentence.find(name), name) for name in names if name in sentence]
            actor = min(actors)[1] if actors else next(
                (s['name'] for s in subjects if s['id'] != self_id), '')
            if actor and actor != IDENTITY.creator:
                issues.append('wrong_creator_or_self_relationship')
    if re.search(r'我.{0,10}(?:负责|主持).{0,8}(?:研发|制造|维护).{0,6}(?:终端|Friston-3)', text, re.I):
        issues.append('reversed_creator_relationship')

    # Database entities bound which people a source discusses. This is an
    # entity-coverage check, not proof of every proposition involving them.
    for sentence in re.split(r'[。！？!?；;]', text):
        if not sentence.strip():
            continue
        qualified = bool(_QUALIFIED.search(sentence))
        sentence_sources = [s for s in sources if _usable_source(s, qualified=qualified)]
        support = _support_text(sentence_sources)
        supported_ids = {s.get(key) for s in sentence_sources
                         for key in ('subject_id', 'object_id') if s.get(key)}
        if not source_excerpt and uncertain and not usable and not qualified:
            issues.append('unqualified_uncertain_evidence')
        # An uncertain sentence's qualification must not license a separate,
        # unqualified assertion elsewhere in the reply.
        for entity in (() if source_excerpt else mentioned_entities):
            if entity['id'] == self_id or entity['name'] not in sentence:
                continue
            if _name_only_utterance(sentence, entity['name']):
                continue
            if (any(item.get('id') == entity['id'] for item in candidate_entities)
                    and _candidate_question(sentence, candidate_entities, user_text)):
                continue
            if doctor_role and entity['name'] == '博士' and _doctor_address(sentence):
                continue
            if (entity['id'] not in supported_ids and entity['name'] not in support and not (
                    any(subject['id'] == entity['id'] for subject in subjects)
                    and _NONASSERTIVE_RELATION.search(sentence))):
                issues.append('unsupported_named_entity:' + entity['name'])
    # Unattributed first-person quotation must not become the NPC's own voice.
    if not quoted_choice and re.search(r'[“「『"][^”」』"]*(?:我|您)[^”」』"]*[”」』"]', reply):
        quotes = re.findall(r'[“「『"]([^”」』"]*(?:我|您)[^”」』"]*)[”」』"]', reply)
        own_quote = bool(quotes) and all(_supported_self_memory(quote, sources) for quote in quotes)
        if not own_quote and not any(name in text for name in names):
            issues.append('unattributed_character_quote')
    return list(dict.fromkeys(issues))
