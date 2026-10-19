"""Name matching and short conversational reference resolution."""
import re
from friston2.knowledge.lore_text import normalize
from friston2.npc.identity import IDENTITY

def _all_aliases(db):
    rows = [dict(row) for row in db.execute('''SELECT a.alias_norm,a.alias,a.entity_id,a.priority,
      a.origin,e.name,e.kind
      FROM aliases a JOIN entities e ON a.entity_id=e.id
      ORDER BY a.priority DESC,a.entity_id''').fetchall()]
    # A Chinese given-name prefix is useful for names such as 克丽斯腾·莱特.
    # Keep every candidate: shared prefixes must be diagnosed, never guessed.
    for row in tuple(rows):
        if row['origin'] != 'canonical_name' or row['kind'] != 'character':
            continue
        prefix = re.split(r'[·•]', row['name'], maxsplit=1)[0]
        if prefix == row['name'] or not re.fullmatch(r'[\u3400-\u9fff]{2,}', prefix):
            continue
        rows.append({**row, 'alias': prefix, 'alias_norm': normalize(prefix),
                     'priority': 35, 'origin': 'name_prefix'})
    return rows


_GENERIC_SPEAKER_ROLES = (
    '领袖', '领导', '老板', '负责人', '主任', '主管', '总工', '工程师', '研究员',
    '科学家', '医生', '护士', '病人', '患者', '干员', '队长', '队员', '士兵',
    '卫兵', '警察', '警官', '商人', '店主', '店员', '工作人员', '助手', '助理',
    '秘书', '实习生', '老师', '学生', '村民', '居民', '路人', '男人', '女人',
    '老人', '孩子', '男孩', '女孩', '青年', '少女', '少年', '父亲', '母亲',
    '哥哥', '姐姐', '弟弟', '妹妹', '客人', '观众', '记者', '市民', '感染者',
    '士官', '军官', '将军', '贵族', '代表', '仆人', '佣兵', '信使', '骑士',
)


def _explicit_speaker_reference(question, alias):
    """An occupational title is a speaker only when queried as a name/title."""
    q, word = normalize(question), normalize(alias)
    if q == word:
        return True
    escaped = re.escape(word)
    if re.search(r'[“「『"\']\s*' + re.escape(alias) + r'\s*[”」』"\']', question, re.I):
        return True
    if re.search(r'(?:叫|名叫|称作|说话人|角色|人物)' + escaped, q):
        return True
    return bool(re.fullmatch(
        r'(?:请)?(?:(?:介绍|说说|聊聊|认识|了解|查询|查查)(?:一下)?)?' + escaped +
        r'(?:是谁|是什么人|是什么角色|的档案|这个角色|这个人物|吗)?', q))


def _generic_speaker_label(name):
    name = normalize(name)
    roles = '|'.join(re.escape(role) for role in _GENERIC_SPEAKER_ROLES)
    return bool(re.fullmatch(
        r'(?:(?:年轻|年老|陌生|神秘|疲惫|受伤|普通|男性|女性|年幼|中年|资深|高级|当地)的?)?'
        r'(?:' + roles + r')(?:[甲乙丙丁一二三四五0-9a-z]+)?', name))


def _self_relation_query(question):
    """Recognize either direction, without treating '你和我聊聊X' as a pair."""
    q = normalize(question)
    tail = (r'(?:到底|究竟)?(?:是|有|算是)?(?:什么关系|何关系|啥关系|怎样的?关系|何种关系|'
            r'关系如何|关系怎样|关系好吗|有关系吗|认识吗|见过吗|见过面吗|熟吗|是朋友吗|朋友吗|同事吗|搭档吗)')
    for pattern in (r'你(?:和|与|跟)(.{1,45}?)' + tail,
                    r'(.{1,45}?)(?:和|与|跟)你' + tail):
        match = re.search(pattern, q)
        if match and not any(word in match.group(1) for word in ('聊聊', '说说', '介绍', '讲讲', '谈谈', '告诉')):
            return True
    return False


def asks_personal_acquaintance(question):
    """Knowing about a person is distinct from claiming a personal encounter."""
    q = normalize(question)
    return (_self_relation_query(question)
            or bool(re.search(r'你(?:以前|曾经|曾|真的|确实|亲自|私下|本人|当面|在现实中)*(?:见过|遇见过|遇到过|结识过)', q))
            or bool(re.search(r'你(?:以前|曾经|真的|确实|亲自|私下|本人|当面)(?:认识|认不认识)', q))
            or bool(re.search(r'你(?:的)?(?:好?朋友|同伴)|你(?:都|还)?(?:认识|熟悉)(?:谁|哪些人|什么人)', q)))


def _has_person_reference(question):
    """Require pronoun grammar rather than a character inside an unknown name."""
    q = normalize(question)
    if any(word in q for word in ('那位', '那个人')):
        return True
    # '其他人' is a new/unspecified topic; '他拉' could be an unknown name.
    # A standalone ending or a predicate/particle makes the reference explicit.
    return bool(re.search(
        r'(?<!其)[他她它](?:们)?(?=$|的|和|与|跟|呢|吗|吧|呀|啊|是|有|会|能|'
        r'曾经|还|都|又|也|就|却|从|在|为什么|怎么|怎样|什么|说|做|去|见|'
        r'喜欢|讨厌|认识|认为|到|想|后来|以前|过去|最|究竟|到底|值得|性格|经历|情况|本人)', q))


def _ordinary_alias_span(text, alias, start, end):
    """Reject a common-noun occurrence without hiding other name occurrences."""
    if alias not in ('真理', '解真'):
        return False
    # Explicit character-reference wording wins over an ambiguous verb such as
    # "探索" (e.g. "探索真理这个角色的经历").
    if re.match(r'(?:这个|这位|这名)?(?:干员|角色|人物)|是谁|是什么人', text[end:]):
        return False
    if alias == '解真':
        # "了解/理解" + "真实/真相" crosses two ordinary words. Check this
        # occurrence only: a later explicit mention of 解真 must still resolve.
        return (start > 0 and text[start - 1] in ('了', '理')
                and text[end:end + 1] in ('实', '相'))
    return bool(re.search(r'(?:追求|追寻|探寻|探索|寻求|揭示|宇宙|科学|唯一|普遍|客观|绝对|终极)(?:的|中的)?$',
                          text[:start]))


def resolve_entity_resolution(db, question, previous_entities=()):
    """Return explicit candidates and ambiguity, plus bounded pronoun resolution."""
    q = normalize(question)
    candidates = {}
    mentions, ambiguities = [], []
    groups = {}
    for row in _all_aliases(db):
        groups.setdefault(row['alias_norm'], []).append(row)
    matched_spans = []
    for word in sorted(groups, key=len, reverse=True):
        rows = groups[word]
        if word not in q:
            continue
        spans = [(m.start(), m.end()) for m in re.finditer(re.escape(word), q)]
        spans = [(a,b) for a,b in spans if not any(x <= a and b <= y for x,y in matched_spans)]
        spans = [(a,b) for a,b in spans if not _ordinary_alias_span(q, word, a, b)]
        if not spans:
            continue
        priority = max(row['priority'] for row in rows)
        best = list({row['entity_id']: row for row in rows if row['priority'] == priority}.values())
        row = best[0]
        if row['kind'] == 'event' and len(word) <= 4:
            # Short story titles such as “合作”“改变”“自我” are common
            # words, not named people in an ordinary sentence.
            named_story = (re.search(r'[《“「]' + re.escape(row['alias']) + r'[》”」]', question)
                           or re.search(r'(?:活动|剧情|密录|故事)' + re.escape(word), q)
                           or _explicit_speaker_reference(question, row['alias']))
            if not named_story:
                continue
        if (row['kind'] == 'dialogue_speaker' and _generic_speaker_label(row['name'])
                and not _explicit_speaker_reference(question, row['alias'])):
            continue
        if len(word)==1 and '\u3400' <= word <= '\u9fff':
            # One-character operators (e.g. 陈, 令) match only in explicit
            # name-reference grammar, never by incidental substring.
            w=re.escape(word)
            inquiry = r'说说|谁是|介绍|认识|关于|聊聊|了解|知道|知不知道|怎么看|觉得|认为|评价'
            tail = r'(?:是|的|和|与|跟|呢|吗|谁|怎么样|怎样|如何|$)'
            if not (re.search(r'^(?:你)?(?:' + inquiry + r')?(?:一下)?' + w + tail, q)
                    or re.search(r'(?:' + inquiry + r'|叫|和|与|跟)(?:一下)?' + w + tail, q)):
                continue
        # Avoid substring false positives from short latin alias names.
        if re.fullmatch(r'[a-z0-9_]+', word) and len(word) <= 3:
            if not re.search(r'(?<![a-z0-9_])' + re.escape(word) + r'(?![a-z0-9_])', q):
                continue
        matched_spans.extend(spans)
        if len(best) != 1:
            ambiguities.append({'alias': row['alias'], 'candidates': [
                {'id': item['entity_id'], 'name': item['name'], 'alias': item['alias']}
                for item in best]})
            continue
        score = len(word) * 100 + row['priority']
        existing = candidates.get(row['entity_id'], -1)
        if score > existing:
            candidates[row['entity_id']] = score
            mentions.append({'id': row['entity_id'], 'name': row['name'],
                             'alias': row['alias'], 'origin': row['origin']})
    ids = [key for key, _ in sorted(candidates.items(), key=lambda p: -p[1])]
    # A fuller name in this same question can settle its accompanying title.
    # Previous discussion alone cannot decide a title shared across timelines.
    unresolved = []
    for ambiguity in ambiguities:
        named = [item for item in ambiguity['candidates'] if item['id'] in ids]
        if len(named) != 1:
            unresolved.append(ambiguity)
    ambiguities = unresolved
    self_patterns = ('你是谁', '你的研发', '你的制造', '你的主人',
                     '谁制造了你', '谁设计了你', '你谁造', '谁造了你', '你的主管', '你的老板',
                     '谁制造你', '谁设计你', '谁研发你', '谁研发了你',
                     '介绍一下自己', '介绍自己', '自我介绍', '你最喜欢', '你去过',
                     '你经历过', '你的回忆', '你的维护')
    self_relation = asks_personal_acquaintance(question)
    self_social = bool(re.search(
        r'你的(?:过去|往事|经历|亲人|家人)|你(?:见过|去过|经历过)|你有什么经历', question))
    if any(s in question for s in self_patterns) or self_relation or self_social:
        ids = [IDENTITY.entity_id] + [i for i in ids if i != IDENTITY.entity_id]
    person_reference = _has_person_reference(question)
    followup = (person_reference
                or any(s in question for s in ('刚才', '继续', '接着', '然后呢'))
                or q in ('为什么', '为什么呢', '为什么会这样', '为什么这么觉得', '为什么这样说',
                         '为什么这样觉得', '为什么这么说', '怎么说', '怎么说呢',
                         '具体说说', '具体说说呢', '具体说说吧', '后来呢', '后来怎么样'))
    # Explicit names replace the topic unless a pronoun clearly denotes a
    # second participant ("她和博士"). Avoid dragging an unrelated old name
    # into a fresh question such as "可露希尔她是什么人".
    needs_antecedent = not ids or any(s in question for s in (
        '她和', '他和', '它和', '她与', '他与', '它与', '她跟', '他跟', '它跟',
        '和她', '和他', '和它', '与她', '与他', '与它', '跟她', '跟他', '跟它'))
    needs_antecedent = needs_antecedent or (self_relation and person_reference)
    used_previous = False
    if followup and needs_antecedent and not ambiguities:
        # Third-person pronouns prefer the discussed other person over self.
        previous = sorted(previous_entities, key=lambda item: item == IDENTITY.entity_id)
        for entity_id in previous:
            if entity_id not in ids and db.execute('SELECT 1 FROM entities WHERE id=?', (entity_id,)).fetchone():
                ids.append(entity_id)
                used_previous = True
                break
    ids = ids[:5]
    return {'status': 'ambiguous' if ambiguities else 'resolved' if ids else 'unknown',
            'entity_ids': ids, 'mentions': mentions, 'ambiguities': ambiguities,
            'used_previous': used_previous}


def resolve_entities(db, question, previous_entities=()):
    """Compatibility API; inspect resolve_entity_resolution for ambiguity."""
    return resolve_entity_resolution(db, question, previous_entities)['entity_ids']


