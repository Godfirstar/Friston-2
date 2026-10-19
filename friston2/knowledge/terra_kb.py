"""Offline source-attributed Wiki relationship graph, build/query/export CLI."""
from __future__ import annotations

import argparse
from collections import deque
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from friston2.config import KNOWLEDGE_DB, LORE_WIKI_SOURCE
from friston2.knowledge.entity_resolver import resolve_entities, resolve_entity_resolution
from friston2.knowledge.import_lore_wiki import import_lore_wiki
from friston2.knowledge.lore_schema import DDL, DB_VERSION
from friston2.knowledge.lore_text import normalize
from friston2.knowledge.retrieve_lore import build_evidence_packet, detect_intent, get_card, ranked_search
from friston2.npc.identity import IDENTITY

DEFAULT_SOURCE = LORE_WIKI_SOURCE
TABLES = ('entities', 'aliases', 'sources', 'passages', 'relations', 'event_mentions', 'import_issues')


def build_db(*, source=None, db_file=KNOWLEDGE_DB):
    """Validate a complete new snapshot before atomically replacing only lore DB."""
    source = Path(source) if source is not None else DEFAULT_SOURCE
    db_file = Path(db_file).resolve()
    db_file.parent.mkdir(parents=True, exist_ok=True)
    fd, filename = tempfile.mkstemp(prefix=db_file.stem + '-', suffix='.building.db', dir=db_file.parent)
    os.close(fd)
    temp = Path(filename)
    try:
        with closing(sqlite3.connect(temp)) as db:
            db.executescript(DDL)
            with db:
                metrics = import_lore_wiki(db, source)
                for table in TABLES:
                    metrics[table] = db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                db.executemany('INSERT INTO meta VALUES(?,?)', (
                    ('schema', DB_VERSION), ('import_status', 'wiki_import_complete'),
                    ('import_stats', json.dumps(metrics, ensure_ascii=False)),
                ))
                if db.execute('PRAGMA foreign_key_check').fetchall():
                    raise ValueError('图谱包含悬空引用，未替换旧库。')
                if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('SQLite 完整性检查未通过，未替换旧库。')
        try:
            os.replace(temp, db_file)
        except PermissionError as exc:
            raise PermissionError(f'请先退出 main.py 或关闭 SQLite 查看器后再构建：{db_file}') from exc
    finally:
        temp.unlink(missing_ok=True)
    return metrics


def connect(db_file=KNOWLEDGE_DB):
    path = Path(db_file).resolve()
    if not path.is_file():
        raise FileNotFoundError('知识库尚未构建，请运行 python -m friston2.knowledge.terra_kb build')
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try:
        version = db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
        if not version or version[0] != DB_VERSION:
            raise RuntimeError('知识库版本已更换，请退出 main.py 后运行 python -m friston2.knowledge.terra_kb build')
    except Exception:
        db.close()
        raise
    return db


def _row_entity(db, entity_id):
    row = db.execute('SELECT * FROM entities WHERE id=?', (entity_id,)).fetchone()
    return dict(row) if row else None


def retrieve_packet(question, *, previous_entities=(), retrieval_question=None,
                    db_file=KNOWLEDGE_DB, max_chars=2500, **_compat):
    if not isinstance(question, str) or not question.strip():
        return {'context': '', 'intent': 'general', 'entity_ids': [], 'subjects': [], 'sources': [],
                'resolution': {'status': 'unknown', 'entity_ids': [], 'mentions': [],
                               'ambiguities': [], 'used_previous': False}}
    with closing(connect(db_file)) as db:
        resolution = resolve_entity_resolution(db, question, previous_entities)
        ids = list(resolution['entity_ids'])
        query = retrieval_question if isinstance(retrieval_question, str) and retrieval_question.strip() else question
        if resolution['status'] == 'ambiguous':
            return {'context': '', 'intent': detect_intent(query), 'entity_ids': ids,
                    'subjects': [{key: row[key] for key in ('id', 'name', 'kind', 'scope')}
                                 for entity_id in ids if (row := _row_entity(db, entity_id))],
                    'sources': [], 'resolution': resolution}
        if normalize(question) in ('为什么', '为什么呢', '怎么说', '具体说说', '为什么这么说') and detect_intent(query) == 'relationship':
            for entity_id in previous_entities:
                if entity_id not in ids and _row_entity(db, entity_id):
                    ids.append(entity_id)
        packet = build_evidence_packet(db, query, ids, max_chars=max_chars)
        packet['entity_ids'] = ids
        packet['resolution'] = {**resolution, 'entity_ids': list(ids)}
        if IDENTITY.entity_id in ids and len(ids) >= 2 and not packet['sources']:
            others = [entity_id for entity_id in ids if entity_id != IDENTITY.entity_id]
            names = [row['name'] for entity_id in others if (row := _row_entity(db, entity_id))]
            overview = build_evidence_packet(db, '介绍' + '、'.join(names), others, max_chars=700)
            packet['target_overviews'] = [source for source in overview['sources'] if source['kind'] == 'overview']
        return packet


def retrieve_context(question, **kwargs):
    packet = retrieve_packet(question, **kwargs)
    return packet['context'], packet['entity_ids']


def context_for(question, limit=2):
    return retrieve_context(question, max_relations=limit)[0]


def search_passages(query, *, db=None, db_file=KNOWLEDGE_DB, limit=3, kinds=None, focus_names=()):
    owns = db is None
    db = db or connect(db_file)
    try:
        resolution = resolve_entity_resolution(db, query)
        if resolution['status'] == 'ambiguous':
            return []
        ids = list(resolution['entity_ids'])
        for name in focus_names:
            ids.extend(i for i in resolve_entities(db, name) if i not in ids)
        return ranked_search(db, query, ids=ids, limit=limit, kinds=kinds)
    finally:
        if owns:
            db.close()


def reply_entity_mentions(reply, *, db_file=KNOWLEDGE_DB):
    with closing(connect(db_file)) as db:
        return [{'id': entity['id'], 'name': entity['name']}
                for entity_id in resolve_entities(db, reply)
                if (entity := _row_entity(db, entity_id)) and len(entity['name']) >= 2
                and entity['name'] in reply
                and entity['kind'] in ('character', 'machine', 'fan_machine', 'dialogue_speaker')]


def lookup(question, limit=3, *, db_file=KNOWLEDGE_DB, **_compat):
    with closing(connect(db_file)) as db:
        return [{'id': e['id'], 'fact': e['summary'], 'kind': '项目设定' if e['scope']=='fan' else 'Wiki二次总结',
                 'source': e['source']} for i in resolve_entities(db, question)[:limit]
                if (e := _row_entity(db, i))]


def stats(db_file=KNOWLEDGE_DB):
    with closing(connect(db_file)) as db:
        result = {t: db.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in TABLES}
        result['by_kind'] = dict(db.execute('SELECT kind,count(*) FROM entities GROUP BY kind'))
        result['relations_by_status'] = dict(db.execute('SELECT status,count(*) FROM relations GROUP BY status'))
        result['issues_by_reason'] = dict(db.execute('SELECT reason,count(*) FROM import_issues GROUP BY reason'))
        result['import_stats'] = json.loads(db.execute("SELECT value FROM meta WHERE key='import_stats'").fetchone()[0])
        return result


def network(query, *, db_file=KNOWLEDGE_DB, depth=1, limit=30):
    """Bounded neighborhood for inspection, never a new inferred relationship."""
    depth, limit = min(3, max(0, int(depth))), min(500, max(1, int(limit)))
    with closing(connect(db_file)) as db:
        roots = resolve_entities(db, query)[:2]
        visited, edges, queue = set(roots), {}, deque((i, 0) for i in roots)
        truncated = False
        while queue:
            entity_id, distance = queue.popleft()
            if distance >= depth:
                continue
            rows = db.execute('''SELECT r.*,a.name AS src_name,b.name AS dst_name
                FROM relations r JOIN entities a ON a.id=r.src JOIN entities b ON b.id=r.dst
                WHERE r.src=? OR r.dst=?
                ORDER BY CASE WHEN r.src=? THEN 0 ELSE 1 END,r.id''', (entity_id, entity_id, entity_id))
            for row in rows:
                if row['id'] in edges:
                    continue
                if len(edges) >= limit:
                    truncated = True
                    break
                edges[row['id']] = dict(row)
                for target in (row['src'], row['dst']):
                    if target not in visited:
                        visited.add(target)
                        queue.append((target, distance+1))
        return {'roots': roots, 'depth': depth, 'truncated': truncated,
                'note': '箭头表示资料页面主体到关系对象；多跳路径不推导人物关系。Wiki为未人工核验的二次总结。',
                'nodes': [_row_entity(db, i) for i in sorted(visited)], 'edges': list(edges.values())}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Friston-3 Wiki 人物关系知识图谱')
    parser.add_argument('operation', nargs='?', default='stats')
    parser.add_argument('question', nargs='*')
    parser.add_argument('--source', help='本地 arknights_lore_wiki 仓库根目录')
    parser.add_argument('--limit', type=int, default=12)
    parser.add_argument('--depth', type=int, default=1)
    parser.add_argument('--output', type=Path, help='导出 UTF-8 JSON 文件')
    args = parser.parse_args(argv)
    query = ' '.join(args.question)
    if args.operation == 'build':
        result = build_db(source=args.source)
    elif args.operation == 'stats':
        result = stats()
    elif args.operation == 'network':
        if not query:
            parser.error('network 后请写人物名')
        result = network(query, depth=args.depth, limit=args.limit)
    elif args.operation in ('profile', 'card', 'relations'):
        if not query:
            parser.error('请写人物名')
        with closing(connect()) as db:
            ids = resolve_entities(db, query)
            if not ids:
                result = {'message': '没有明确匹配到人物，请使用完整名称。'}
            elif args.operation == 'relations':
                slots = ','.join('?' for _ in ids)
                where = f'r.src IN ({slots}) AND r.dst IN ({slots})' if len(ids)>1 else 'r.src=? OR r.dst=?'
                result = [dict(row) for row in db.execute(f'''SELECT r.*,a.name AS src_name,b.name AS dst_name
                    FROM relations r JOIN entities a ON a.id=r.src JOIN entities b ON b.id=r.dst
                    WHERE ({where}) ORDER BY r.id LIMIT ?''', (*ids, *ids, min(500, max(1, args.limit))))]
            else:
                result = get_card(db, ids[0], max_sections=min(10, max(1, args.limit)))
    elif args.operation == 'search':
        if not query:
            parser.error('search 后请写关键词')
        result = search_passages(query, limit=min(30, max(1, args.limit)))
    else:
        result = retrieve_packet(' '.join([args.operation, *args.question]))
    content = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content + '\n', encoding='utf-8')
        print(f'已导出：{args.output.resolve()}')
    else:
        print(content)


if __name__ == '__main__':
    main()
