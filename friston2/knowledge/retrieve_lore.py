"""Question-aware retrieval over source-linked lore summaries and direct edges.

The upstream wiki is an LLM-written secondary source. Its assertions remain
source assertions, not independently verified game dialogue. Relation direction
records the narrative owner and target, never an inferred parent/child direction.
"""
from __future__ import annotations

import math
import re

from friston2.knowledge.lore_text import normalize, phrase_query, tokenize
from friston2.knowledge.entity_resolver import asks_personal_acquaintance
from friston2.npc.identity import IDENTITY


_KINDS = ('profile', 'character', 'relationship', 'event', 'project')
_CATEGORIES = {
    'introduction': ('identity', 'background', 'personality'),
    'personality': ('personality', 'background', 'identity'),
    'relationship': ('relationship',),
    'leadership': ('identity', 'relationship', 'background'),
    'experience': ('experience', 'event', 'event_summary', 'background'),
    'concept': ('identity', 'background', 'event_summary'),
    'general': ('identity', 'personality', 'background'),
}
_RELATION_TOPICS = ('父亲', '母亲', '父母', '亲属', '朋友', '好友', '老师', '导师',
                    '学生', '上司', '同事', '合作', '兄弟', '姐妹', '师徒', '制造',
                    '研发', '设计', '恋人', '搭档', '监护人', '助手', '下属', '师父', '徒弟')
_PERSONALITY = ('性格', '温柔', '坚定', '信任', '善良', '理想', '愿望', '冷静',
                '认真', '孤独', '固执', '热情', '喜欢', '讨厌', '认为', '信念')
_LEADERSHIP = ('公开领袖', '领导者', '领袖', '负责人', '主管', '指挥', '管理')


def detect_intent(question: str) -> str:
    q = normalize(question)
    if q in ('知道了', '我知道了', '了解了', '我了解了', '你知道吗', '你知道么'):
        return 'general'
    if asks_personal_acquaintance(question) or re.search(r'.+(?:和|与|跟).+(?:认识|熟悉|熟不熟|见过)(?:吗|么|不)?$', q):
        return 'relationship'
    if any(word in q for word in ('老大', '领袖', '谁领导', '谁负责', '负责人', '老板', '谁管', '谁说了算')):
        return 'leadership'
    relation_topics = tuple(word for word in _RELATION_TOPICS if word not in ('制造', '研发', '设计'))
    if any(word in q for word in ('关系', '谁造', '谁制造', '谁设计', '谁研发', '研发者', *relation_topics)):
        return 'relationship'
    if any(word in q for word in ('怎样的人', '什么样的人', '性格', '怎么看', '看法', '评价',
                                  '你觉得', '这么觉得', '这样觉得', '喜欢谁', '最喜欢', '讨厌', '动机')):
        return 'personality'
    if any(word in q for word in ('剧情', '故事', '经历', '过去', '当年', '发生', '后来',
                                  '为什么', '起因', '结局', '说过', '哪一章', '证据', '原文', '台词')):
        return 'experience'
    if any(word in q for word in ('是什么', '什么意思', '定义', '概念', '原理')):
        return 'concept'
    if (any(word in q for word in ('介绍', '是谁', '谁是', '认识', '了解', '说说', '聊聊', '档案', '详细'))
            or re.search(r'^(?:那|那么|请问)?你?(?:知不知道|知道).+', q)):
        return 'introduction'
    return 'general'


def _rows(db, sql, params=()):
    cursor = db.execute(sql, params)
    names = [item[0] for item in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _entity(db, entity_id):
    rows = _rows(db, 'SELECT * FROM entities WHERE id=?', (entity_id,))
    return rows[0] if rows else None


def _terms(question, names=()):
    residual = str(question)
    for name in sorted(names, key=len, reverse=True):
        residual = re.sub(re.escape(name), ' ', residual, flags=re.I)
    for word in ('发生过什么', '是怎样的人', '是什么样的人', '什么关系', '有何关系', '是什么',
                 '什么意思', '介绍一下', '你觉得', '你认识', '你知道', '给我讲讲', '告诉我',
                 '怎么看', '为什么', '谁是', '是谁', '介绍', '说说', '聊聊', '了解', '认识',
                 '一下', '详细', '原文', '档案', '关系', '性格', '故事', '经历', '剧情',
                 '那位', '那个人', '她', '他', '它', '你', '的', '和', '与', '跟', '吗', '呢', '啊', '吧'):
        residual = residual.replace(word, ' ')
    return list(dict.fromkeys(re.findall(r'[\u3400-\u9fff]{2,}|[A-Za-z0-9_]{2,}', residual)))[:6]


def _exact_ids(db, query):
    """Only unambiguous exact aliases; conversation resolution lives in terra_kb."""
    result = []
    for value in dict.fromkeys([normalize(query), *map(normalize, _terms(query))]):
        rows = _rows(db, 'SELECT DISTINCT entity_id FROM aliases WHERE alias_norm=?', (value,))
        if len(rows) == 1 and rows[0]['entity_id'] not in result:
            result.append(rows[0]['entity_id'])
    return result[:3]


def _concept_definition(query, content):
    """A character merely mentioning a term is not its definition."""
    match = re.fullmatch(r'(?:请问|请解释|解释一下)?(.{1,20}?)(?:是什么|是什么意思|的定义|的概念)[？?。\s]*', query)
    if not match:
        return False
    term = re.escape(match.group(1).strip())
    return bool(re.search(term + r'(?:是指|指的是|是一种|是一类|是一系列)', content))


def _relations(db, ids, intent='relationship', question='', limit=20):
    if not ids:
        return []
    slots = ','.join('?' for _ in ids)
    # Two named people ask about their own edge, never arbitrary neighbours.
    operator = 'AND' if intent == 'relationship' and len(ids) >= 2 else 'OR'
    rows = _rows(db, f'''SELECT r.*,a.name AS src_name,b.name AS dst_name,
        s.source_type,s.path AS source_path,s.url AS source_url,s.revision,
        p.line_start,p.line_end
        FROM relations r JOIN entities a ON a.id=r.src JOIN entities b ON b.id=r.dst
        LEFT JOIN sources s ON s.id=r.source_id LEFT JOIN passages p ON p.id=r.passage_id
        WHERE r.src IN ({slots}) {operator} r.dst IN ({slots})''', (*ids, *ids))
    terms = _terms(question, [e['name'] for i in ids if (e := _entity(db, i))])
    topics = [word for word in _RELATION_TOPICS if word in question]
    if intent == 'relationship' and len(ids) == 1 and topics and IDENTITY.entity_id not in ids:
        rows = [row for row in rows if any(word in row['description'] for word in topics)]
    for row in rows:
        text = row['description']
        score = 45 * (row['src'] in ids) + 55 * (row['src'] in ids and row['dst'] in ids)
        score += 15 * sum(word in text for word in terms)
        score += 12 * sum(word in text for word in topics)
        score += 8 if row['status'] == 'project' else 0
        score -= 18 if row['status'] == 'uncertain' else 0
        if intent == 'personality':
            score += 4 * sum(word in text for word in _PERSONALITY)
        if intent == 'leadership':
            score += 20 * sum(word in text for word in _LEADERSHIP)
        row['score'] = score
    return sorted(rows, key=lambda row: (-row['score'], row['line_start'] or 0,
                                        row['passage_id'], row['id']))[:max(1, int(limit))]


def ranked_search(db, query, ids=None, limit=3, kinds=None):
    """Rank owner sections, direct relationships and explicit event mentions.

    Event mentions retrieve narrative context; they never establish participation
    or a personal relationship. An explicit empty ids list disables name inference.
    """
    limit = max(1, min(30, int(limit)))
    allow = tuple(dict.fromkeys(_KINDS if kinds is None else kinds))
    if not allow:
        return []
    entities = [e for i in dict.fromkeys(_exact_ids(db, query) if ids is None else ids)
                if (e := _entity(db, i))][:3]
    ids = [e['id'] for e in entities]
    names = [e['name'] for e in entities]
    intent = detect_intent(query)
    # A biography request needs that person's own passages. Filter before the
    # result/count and packet budgets, so cross-page mentions cannot crowd out
    # useful owner sections. An explicitly named event can still supply context.
    person_experience = (intent == 'experience' and bool(entities) and
                         all(e['kind'] in ('character', 'machine', 'fan_machine', 'dialogue_speaker')
                             for e in entities))
    if not ids and intent in ('introduction', 'personality', 'relationship', 'leadership'):
        # A passage mentioning an unresolved person is not that person's card.
        return []
    terms = _terms(query, names)
    meaningful = set(tokenize(' '.join(terms)).split())
    slots = ','.join('?' for _ in allow)
    select = '''SELECT p.*,s.source_type,s.path AS source_path,s.url AS source_url,s.revision
                FROM passages p LEFT JOIN sources s ON s.id=p.source_id'''
    candidates = {}

    def remember(rows):
        candidates.update((row['id'], row) for row in rows)

    if intent == 'relationship' and len(ids) >= 2:
        direct = _relations(db, ids, intent, query, limit=30)
        for relation in direct:
            remember(_rows(db, select + f' WHERE p.id=? AND p.kind IN ({slots})',
                           (relation['passage_id'], *allow)))
    else:
        for entity_id in ids:
            remember(_rows(db, select + f' WHERE p.ref_id=? AND p.kind IN ({slots}) ORDER BY p.id LIMIT 200',
                           (entity_id, *allow)))
            if intent == 'experience' and not person_experience:
                remember(_rows(db, select + f''' WHERE p.id IN (
                    SELECT passage_id FROM event_mentions WHERE entity_id=? AND basis='explicit_name')
                    AND p.kind IN ({slots}) ORDER BY p.id LIMIT 80''', (entity_id, *allow)))
        clauses = [phrase_query(term) for term in [*terms, *names] if phrase_query(term)]
        expressions = ([' AND '.join(f'({term})' for term in clauses[:2])] if len(clauses) >= 2 else []) + clauses[:4]
        for expression in dict.fromkeys(expressions):
            remember(_rows(db, f'''SELECT p.*,s.source_type,s.path AS source_path,s.url AS source_url,
                    s.revision,bm25(passage_fts) AS fts_score
                FROM passage_fts JOIN passages p ON p.id=passage_fts.rowid
                LEFT JOIN sources s ON s.id=p.source_id
                WHERE passage_fts MATCH ? AND p.kind IN ({slots}) ORDER BY fts_score LIMIT 60''',
                (expression, *allow)))
    scored = []
    for item in candidates.values():
        content = item['content']
        owner = item['ref_id'] in ids
        category = item['category']
        if person_experience and not owner:
            continue
        if intent == 'concept' and not ids and not _concept_definition(query, content):
            continue
        if ids and intent in ('introduction', 'general', 'personality', 'concept') and not owner:
            continue
        if ids and not owner and not any(name in content for name in names):
            continue
        if intent != 'relationship' and category == 'relationship':
            continue  # Use typed edges so uncertainty cannot be lost in a passage.
        if intent == 'relationship' and len(ids) == 1 and category != 'relationship':
            continue
        score = 40 * owner
        score += 6 * sum(name in content for name in names)
        if category in _CATEGORIES[intent]:
            score += 36 - 8 * _CATEGORIES[intent].index(category)
        if meaningful:
            tokens = set(tokenize(content + ' ' + item['section']).split())
            score += 28 * len(meaningful & tokens) / len(meaningful)
            score += 12 * sum(word in content for word in terms)
        score += min(5, math.log1p(abs(float(item.pop('fts_score', 0) or 0))))
        if intent == 'personality':
            score += min(22, 3 * sum(word in content for word in _PERSONALITY))
        if intent == 'leadership':
            score += min(30, 10 * sum(word in content for word in _LEADERSHIP))
        item['score'] = round(score, 3)
        scored.append(item)
    scored.sort(key=lambda row: (-row['score'], row['id']))
    output, seen = [], set()
    for item in scored:
        key = normalize(item['content'])
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
        if len(output) >= limit:
            break
    return output


def get_card(db, entity_id, max_sections=3):
    entity = _entity(db, entity_id)
    if not entity:
        return None
    card = dict(entity)
    card['sections'] = {}
    for row in _rows(db, '''SELECT p.*,s.source_type,s.path AS source_path,s.url AS source_url,s.revision
            FROM passages p LEFT JOIN sources s ON s.id=p.source_id WHERE p.ref_id=? ORDER BY p.id''', (entity_id,)):
        group = card['sections'].setdefault(row['category'], [])
        if len(group) < max(1, int(max_sections)):
            group.append(row)
    card['relations'] = _relations(db, [entity_id], limit=1000)
    card['relationship_candidates'] = [row for row in card['relations'] if row['status'] == 'uncertain']
    card['event_mentions'] = _rows(db, '''SELECT DISTINCT em.event_id,e.name,em.passage_id,em.basis
        FROM event_mentions em JOIN entities e ON e.id=em.event_id
        WHERE em.entity_id=? ORDER BY em.event_id,em.passage_id LIMIT 30''', (entity_id,))
    return card


def _complete_excerpt(text, budget):
    text = re.sub(r'\s+', ' ', str(text or '')).strip()
    if len(text) <= budget:
        return text
    ends = [match.end() for match in re.finditer(r'[。！？!?；;](?:[”」』])?', text)
            if match.end() <= budget]
    return text[:max(ends)].strip() if ends else ''


def _narrative_excerpt(text):
    """Preserve factual narration and quotations under their source-owned label.

    Quotation marks also wrap project names, and first-person words can occur in
    third-person narration. Removing those entire sentences destroys evidence.
    The enclosing evidence block carries the owner; quoted 'I' is source text,
    never independent proof of the current speaker's memories.
    """
    return re.sub(r'\s+', ' ', str(text or '')).strip()


def _citation(item):
    path = item.get('source_path') or item.get('source_id') or '来源未登记'
    # Files and line numbers are enough for the small prompt. Full URLs and the
    # pinned revision are retained in the parallel source record for inspection.
    path = str(path).replace('\\', '/')
    if '/data/' in path:
        path = 'data/' + path.split('/data/', 1)[1]
    if item.get('line_start'):
        path += ':' + str(item['line_start'])
        if item.get('line_end') != item['line_start']:
            path += '-' + str(item['line_end'])
    return path


def _provenance(item):
    project = item.get('source_type') == 'project_config' or item.get('status') == 'project'
    return {key: item.get(key) for key in ('source', 'source_id', 'source_path', 'source_url',
            'revision', 'line_start', 'line_end')} | {
        'source_type': 'project_config' if project else 'llm_summary',
        'status': 'project' if project else item.get('status', 'source_asserted'),
        'verified': bool(item.get('verified', True)) if project else False,
    }


def build_evidence_packet(db, question, ids, max_chars=1900):
    """Return bounded, subject-owned evidence with honest secondary provenance."""
    max_chars = max(0, int(max_chars))
    intent = detect_intent(question)
    entities = [e for i in dict.fromkeys(ids) if (e := _entity(db, i))][:3]
    focused = [e['id'] for e in entities]
    blocks, sources = [], []
    used_text, used_passages = set(), set()
    pair_question = intent == 'relationship' and len(focused) >= 2
    quote_requested = any(word in question for word in ('语音', '台词', '原文', '引用', '说过'))

    def remaining():
        return max_chars - len('\n'.join(blocks)) - bool(blocks)

    def add_evidence(item, label, raw_text, cap, **evidence):
        metadata = _provenance(item)
        state = ('项目同人配置' if metadata['source_type'] == 'project_config' else
                 '模型生成摘要；存疑' if metadata['status'] == 'uncertain' else '模型生成摘要；未独立核验')
        prefix, suffix = f'[{label}｜{state}] ', f' [出处：{_citation(item)}]'
        excerpt = _complete_excerpt(raw_text, min(cap, remaining() - len(prefix) - len(suffix)))
        if not excerpt or normalize(excerpt) in used_text:
            return False
        block = prefix + excerpt + suffix
        blocks.append(block)
        used_text.add(normalize(excerpt))
        sources.append({**metadata, **evidence, 'text': excerpt, 'block': block})
        return True

    # Generic chats should not consult the lore database. Domain questions
    # without named entities are permitted to use source-bound passages.
    lore_topics = ('科研', '伦理', '莱茵生命', '罗德岛', '矿石病', '源石',
                   '天灾', '孤星', '泰拉', '干员', '人物', '组织', '世界观', '剧情')
    topic_lore = any(term in question for term in lore_topics)
    self_memory = any(word in question for word in ('你去过', '你经历', '你的经历', '你的过去', '你还记得', '我们上次'))
    self_acquaintance = bool(re.search(r'你(?:都|还|最)?(?:认识|了解|知道|熟悉)(?:谁|哪些人|什么人|多少人|哪几位|哪些角色)', question))
    allow_without_entity = (intent == 'concept' or
                            (intent in ('general', 'experience') and topic_lore))
    if not entities and (not allow_without_entity or self_memory or self_acquaintance):
        return {'context': '', 'intent': intent, 'subjects': [], 'sources': [],
                'lore_question': topic_lore}

    relations = _relations(db, focused, intent, question, limit=12)

    def add_relations(limit):
        for relation in relations[:limit]:
            raw = relation['description']
            # The label makes narrative ownership explicit without converting
            # source->target into a typed kinship direction.
            label = f"{relation['src_name']}的档案提及{relation['dst_name']}｜人物关系"
            if add_evidence(relation, label, raw, 370,
                    id=f"relation:{relation['id']}", kind='relation', scope=relation['scope'],
                    subject_id=relation['src'], subject_name=relation['src_name'],
                    object_id=relation['dst'], object_name=relation['dst_name'],
                    target_label=relation.get('target_label'), relation=relation['relation'],
                    passage_id=relation['passage_id'], evidence_role='direct_relationship'):
                used_passages.add(relation['passage_id'])

    if intent in ('relationship', 'leadership'):
        add_relations(4 if pair_question else 3)
    if not pair_question:
        for entity in entities:
            if entity['id'] == IDENTITY.entity_id and intent == 'personality':
                continue
            if entity.get('coverage') == 'reference_only':
                add_evidence(entity, entity['name'] + '｜资料覆盖', '只有被提及的名称索引，没有独立档案。', 90,
                             id=f"coverage:{entity['id']}", kind='coverage', scope=entity['scope'],
                             subject_id=entity['id'], subject_name=entity['name'])
                continue
            rows = _rows(db, '''SELECT p.*,s.source_type,s.path AS source_path,s.url AS source_url,s.revision
                FROM passages p LEFT JOIN sources s ON s.id=p.source_id
                WHERE p.ref_id=? AND p.category='identity' ORDER BY p.id LIMIT 1''', (entity['id'],))
            item = rows[0] if rows else {**entity, 'source_type': 'project_config' if entity['scope'] == 'fan' else 'llm_summary'}
            summary = _narrative_excerpt(item.get('content') or entity['summary'])
            cap = 330 if intent in ('introduction', 'general', 'concept') else 180
            if add_evidence(item, entity['name'] + '｜简介', summary, cap,
                    id=f"entity:{entity['id']}", kind='overview', scope=entity['scope'],
                    subject_id=entity['id'], subject_name=entity['name'], evidence_role='entity_overview'):
                if item.get('id') is not None and rows:
                    used_passages.add(item['id'])
        if intent in ('introduction', 'personality', 'general'):
            add_relations(2 if intent == 'personality' else 1)

    # Relationship passages would repeat typed edges without their uncertainty
    # status. A missing direct pair has no graph-derived substitute.
    if not pair_question:
        # Local import avoids a circular dependency: hybrid_search itself uses
        # ranked_search and detect_intent from this module.
        from friston2.knowledge.hybrid_search import hybrid_ranked_search
        hits = hybrid_ranked_search(db, question, ids=focused, limit=8,
                                    kinds=tuple(kind for kind in _KINDS if kind != 'relationship'))
        supplied = 0
        for item in hits:
            if item['id'] in used_passages or item['category'] == 'relationship':
                continue
            owner = _entity(db, item['ref_id'])
            if not owner:
                continue
            raw = item['content'] if quote_requested else _narrative_excerpt(item['content'])
            if intent == 'concept' and not focused:
                raw = ' '.join(sentence.strip() for sentence in re.split(r'(?<=[。！？!?])', raw)
                               if _concept_definition(question, sentence))
            if add_evidence(item, owner['name'] + '｜' + item['section'], raw, 440,
                    id=f"passage:{item['id']}", kind=item['kind'], scope=item['scope'],
                    subject_id=owner['id'], subject_name=owner['name'], section=item['section'],
                    passage_id=item['id'],
                    retrieval_channels=item.get('retrieval_channels', ['lexical']),
                    fusion_score=item.get('fusion_score'),
                    evidence_role='event_summary' if item['kind'] == 'event' else 'owner_summary'):
                supplied += 1
            if supplied >= 3:
                break
    if pair_question and not sources:
        note = '未找到这两个人之间的直接关系条目。共同提及、共同关联对象和图上路径都不能证明人物关系。'
        if len(note) <= remaining():
            blocks.append(note)
    return {'context': '\n'.join(blocks), 'intent': intent,
            'subjects': [{key: e[key] for key in ('id', 'name', 'kind', 'scope')} for e in entities],
            'sources': sources, 'lore_question': topic_lore}


def retrieve_organized_context(db, question, ids, max_chars=1900):
    return build_evidence_packet(db, question, ids, max_chars=max_chars)['context']
