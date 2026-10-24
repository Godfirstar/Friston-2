"""Friston-3 Lesson 02: source-preserving Hybrid RAG (lexical + dense + RRF).

This module only reorders existing passages. It never creates relations,
asserts personal acquaintance, or upgrades LLM-written summaries to canon.

Run manually (no automated test files):
    python -m friston3.knowledge.hybrid_search "赫默为什么反思科研伦理" --top-k 5
    python -m friston3.knowledge.hybrid_search "科研理想和伦理争议之间的冲突" --top-k 5
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path

from friston3.knowledge.entity_resolver import resolve_entity_resolution
from friston3.knowledge.lore_text import normalize, tokenize
from friston3.knowledge.retrieve_lore import (
    detect_intent, ranked_search, _subject_names, _mentioned_subjects, _PERSON_KINDS, _CATEGORIES,
    _requested_categories, _event_scope_context, _event_scoped_item,
)
from friston3.knowledge.semantic_index import search as semantic_search
from friston3.knowledge.terra_kb import connect
from friston3.npc.identity import IDENTITY

RRF_K = 60
SPARSE_CANDIDATES = 30
DENSE_CANDIDATES = 80
MAX_FROM_ONE_FILE = 2


def _diversified(rows, limit, ids=()):
    """Prioritize different documents, then backfill if the corpus is narrow."""
    rows = list(rows)
    if len(ids) >= 2:
        reserved = [next((row for row in rows if row['ref_id'] == entity_id), None) for entity_id in ids]
        reserved = [row for row in reserved if row is not None]
        reserved_ids = {row['id'] for row in reserved}
        rows = reserved + [row for row in rows if row['id'] not in reserved_ids]
    selected, deferred, seen, per_file = [], [], set(), Counter()
    for row in rows:
        content_key = normalize(row['content'])
        if content_key in seen:
            continue
        seen.add(content_key)
        source = row.get('source_path') or row.get('source_id') or str(row['id'])
        if per_file[source] >= MAX_FROM_ONE_FILE:
            deferred.append(row)
        else:
            per_file[source] += 1
            selected.append(row)
        if len(selected) >= limit:
            break
    if len(selected) < limit:
        selected.extend(deferred[:limit - len(selected)])
    return selected[:limit]


def hybrid_ranked_search(db, question, *, ids=(), limit=8, kinds=None, intent=None):
    """Fuse lexical ranking and embedding hits while preserving evidence scope.

    `ids` MUST be the entity resolver's explicit output, including [] for no
    known person. Semantic similarity retrieves context, never creates an edge.
    """
    ids = list(dict.fromkeys(ids))
    limit = max(1, min(30, int(limit)))
    lexical = ranked_search(db, question, ids=ids,
                            limit=max(SPARSE_CANDIDATES, limit), kinds=kinds, intent=intent)
    if ids:
        slots = ','.join('?' for _ in ids)
        entities = {row['id']: dict(row) for row in db.execute(
            f'SELECT id,name,kind FROM entities WHERE id IN ({slots})', ids)}
    else:
        entities = {}
    intent = intent if intent in _CATEGORIES else detect_intent(question, entities.values())

    # A known person's introduction already has a high-confidence owner card.
    # Only a real profile can short-circuit retrieval. Reference-only faction
    # nodes need mentions in other documents, including leadership questions.
    has_profiles = bool(ids) and all(db.execute(
        "SELECT 1 FROM entities WHERE id=? AND coverage!='reference_only'", (entity_id,)
    ).fetchone() for entity_id in ids)
    categories = _requested_categories(question, intent)
    direct_facet = (categories[0] == 'ability' and has_profiles and all(
        any(row['ref_id'] == entity_id and row['category'] == 'ability' for row in lexical)
        for entity_id in ids))
    skip_dense = ((intent == 'introduction' and has_profiles) or direct_facet
                  or (ids == [IDENTITY.entity_id] and intent == 'personality'))
    if skip_dense:
        return _diversified(lexical, limit, ids)

    try:
        # Honor the exact SQLite DB supplied by the caller, not a different
        # default DB whose passage IDs might coincidentally overlap.
        active_db_file = db.execute('PRAGMA database_list').fetchone()[2]
        missing_names = [entity['name'] for entity in entities.values()
                         if normalize(entity['name']) not in normalize(question)]
        semantic_query = ('、'.join(missing_names) + '：' if missing_names else '') + question
        dense_hits = semantic_search(semantic_query, top_k=DENSE_CANDIDATES,
                                     db_file=Path(active_db_file))
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
        print(f'[HybridRAG] 语义索引不可用；安全退回原检索：{exc}')
        return _diversified(lexical, limit, ids)

    labels = _subject_names(db, ids)
    event_scope = _event_scope_context(db, question, list(entities.values()))
    person_focus = bool(entities) and all(e['kind'] in _PERSON_KINDS for e in entities.values())
    allow = set(kinds) if kinds is not None else None

    # Re-fetch FULL SQLite passage records. A dense result alone has no
    # evidence metadata and must never bypass source, owner and type checks.
    passage_ids = [int(hit['id']) for hit in dense_hits]
    if passage_ids:
        slots = ','.join('?' for _ in passage_ids)
        rows = db.execute(f'''SELECT p.*, s.source_type, s.path AS source_path,
                                  s.url AS source_url, s.revision
            FROM passages p LEFT JOIN sources s ON s.id=p.source_id
            WHERE p.id IN ({slots})''', passage_ids)
        by_id = {row['id']: dict(row) for row in rows}
    else:
        by_id = {}

    dense = []
    for hit in dense_hits:
        passage_id = int(hit['id'])
        row = by_id.get(passage_id)
        if row is None:
            continue
        # Relationship passages are eligible dense candidates too. They still
        # pass through the same owner, entity, event-scope and source checks below;
        # a vector hit alone never establishes that a relationship is true.
        row = _event_scoped_item(row, event_scope)
        if row is None:
            continue
        if allow is not None and row['kind'] not in allow:
            continue
        owner = row['ref_id'] in ids
        mentioned = _mentioned_subjects(row, labels) | set(row.get('_event_scope_ids', []))
        if (person_focus and (intent in ('introduction', 'personality') or categories[0] == 'ability')
                and not owner):
            continue
        if ids and not mentioned:
            continue
        if intent == 'relationship' and len(ids) >= 2 and len(mentioned) < 2:
            continue
        row['semantic_similarity'] = hit['score']
        row['matched_subject_ids'] = sorted(mentioned)
        row['evidence_role'] = ('owner_summary' if owner else 'event_context'
                                if row['kind'] == 'event' else 'mentioned_context')
        dense.append(row)

    # 无明确人物时，过滤只有极少查询关键词重合的词法候选。
    # 只在向量检索成功后执行，不影响原来的词法降级路径。
    if not ids and dense:
        query_terms = set(tokenize(question).split())

        if len(query_terms) >= 6:
            lexical = [
                row for row in lexical
                if len(
                    query_terms & set(
                        tokenize(
                            (row.get("section") or "") + " " + row["content"]
                        ).split()
                    )
                ) >= 2
            ]
    # Reciprocal Rank Fusion (RRF) uses rank positions, not incomparable
    # BM25/heuristic scores versus cosine similarities.
    weights = defaultdict(float)
    combined = {}
    channels = defaultdict(set)

    # 没有明确实体时，适度提高语义检索权重。
    # 有明确实体时，仍采用均衡融合。
    semantic_weight = 1.4 if not ids else 1.0

    for channel, hits in (('lexical', lexical), ('semantic', dense)):
        channel_weight = (
            semantic_weight if channel == "semantic" else 1.0
        )

        for rank, row in enumerate(hits, start=1):
            passage_id = int(row["id"])

            weights[passage_id] += (
                    channel_weight / (RRF_K + rank)
            )
            channels[passage_id].add(channel)
            combined.setdefault(passage_id, dict(row))
    fused = []
    for passage_id, row in combined.items():
        item = dict(row)
        item['fusion_score'] = round(weights[passage_id], 7)
        item['retrieval_channels'] = sorted(channels[passage_id])
        # Keep `score` meaningful for callers accustomed to ranked_search.
        item['score'] = item['fusion_score']
        fused.append(item)
    fused.sort(key=lambda row: (-row['fusion_score'], row['id']))
    # Two weak overlapping channels must not bury a strong source-owned
    # lexical answer. Reserve one owner anchor; semantic results add context.
    anchors = [next((row for row in lexical if row['ref_id'] == entity_id), None)
               for entity_id in ids]
    anchors = [row for row in anchors if row is not None]
    output = _diversified(anchors + fused, limit, ids)
    return output


def main():
    parser = argparse.ArgumentParser(description='Friston-3 Hybrid RAG 手动检查入口')
    parser.add_argument('question', nargs='+')
    parser.add_argument('--top-k', type=int, default=5)
    args = parser.parse_args()
    question = ' '.join(args.question)
    with connect() as db:
        resolution = resolve_entity_resolution(db, question)
        if resolution['status'] == 'ambiguous':
            print('人物称呼存在歧义，请使用完整名字：', resolution['ambiguities'])
            return
        rows = hybrid_ranked_search(db, question, ids=resolution['entity_ids'],
                                    limit=args.top_k,
                                    kinds=('profile', 'character', 'event', 'project'))
    for rank, row in enumerate(rows, 1):
        tags = '+'.join(row.get('retrieval_channels', ('lexical',)))
        print(f'[{rank}] RRF={row.get("fusion_score", 0):.5f} ({tags}) | '
              f'{row.get("source_path", "未知来源")}:{row["line_start"]}-{row["line_end"]}')
        print('    ' + row['content'][:150].replace('\n', ' '))
    print('注意：二次 Wiki 总结尚未由原作文本核验；相似度不构成事实证据。')


if __name__ == '__main__':
    main()
