"""Safe, user-directed maintenance of the local Obsidian vault.

Never touches project source files. Does not silently delete records: dismiss
operations move files into an archive and save tombstones to avoid re-creation.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
import re
import shutil

ALIASES = {
    'rrf': 'rrf',
    'rrf按排名融合检索结果': 'rrf',
    '加权倒数排名融合': 'rrf',
    '倒数排名融合': 'rrf',
    'reciprocalrankfusion': 'rrf',
    'weightedrrf': 'rrf',
    '混合检索rrf': 'rrf',
    'rrf倒数排名融合': 'rrf',
    '加权rrf': 'rrf',
}


def key(name):
    cleaned = re.sub(r'[\s_\-—（()）\[\]【】]+', '', str(name)).casefold()
    return ALIASES.get(cleaned, cleaned)


def read_policy(state):
    path = state / 'maintenance.json'
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if isinstance(data, dict):
            return {'ignored_concepts': list(data.get('ignored_concepts') or []),
                    'dismissed_signatures': list(data.get('dismissed_signatures') or [])}
    except (OSError, ValueError, TypeError):
        pass
    return {'ignored_concepts': [], 'dismissed_signatures': []}


def write_policy(state, policy):
    state.mkdir(parents=True, exist_ok=True)
    target = state / 'maintenance.json'
    tmp = target.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(policy, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(target)


def is_ignored_concept(state, name):
    policy = read_policy(state)
    return key(name) in {key(x) for x in policy['ignored_concepts']}


def is_dismissed_version(state, signature):
    return signature[:16] in read_policy(state)['dismissed_signatures']


def is_locked(text):
    """Lock either by frontmatter or a plain Markdown sentinel."""
    if '<!-- RJ:LOCKED -->' in text:
        return True
    front = re.match(r'\A---\s*\n(.*?)\n---\s*\n', text, re.S)
    return bool(front and re.search(r'^maintenance:\s*[\'\"]?(?:locked|ignored)(?:[\'\"])?\s*$', front.group(1), re.I | re.M))


def set_locked(text, locked):
    # No destructive edits: replace a single frontmatter metadata line, or add one.
    flag = 'locked' if locked else 'auto'
    front = re.match(r'\A---\s*\n(.*?)\n---\s*\n', text, re.S)
    if front:
        meta = front.group(1)
        if re.search(r'^maintenance\s*:', meta, re.M):
            meta = re.sub(r'^maintenance\s*:[^\n]*$', 'maintenance: ' + flag, meta, count=1, flags=re.M)
        else:
            meta += '\nmaintenance: ' + flag
        text = '---\n' + meta + '\n---\n' + text[front.end():]
    else:
        text = f'---\nmaintenance: {flag}\n---\n\n' + text
    if not locked:
        text = text.replace('<!-- RJ:LOCKED -->', '')
    return text


def resolve_file(root, label):
    """Only accept a filename/stem within a known subfolder, never an arbitrary path."""
    name = Path(label).name
    if name != label or label in ('', '.', '..') or '/' in label or '\\' in label:
        raise ValueError('只允许提供 Obsidian 笔记文件名，不允许目录路径')
    candidates = [root / name, root / (name + '.md')]
    for p in candidates:
        if p.is_file() and p.suffix.lower() == '.md' and p.parent == root:
            return p
    raise FileNotFoundError(f'找不到笔记：{label}')


def move_archive(path, archive):
    archive.mkdir(parents=True, exist_ok=True)
    target = archive / path.name
    if target.exists():
        when = dt.datetime.now().strftime('%Y%m%d-%H%M%S')
        target = archive / f'{path.stem}-{when}{path.suffix}'
        if target.exists():
            raise FileExistsError('归档文件名冲突，请稍后再试')
    shutil.move(str(path), str(target))
    return target


def manage(action, label, vault, state, update_index=None):
    cards, versions = vault / '02-技术卡片', vault / '01-版本记录'
    archive = vault / '99-归档'
    policy = read_policy(state)
    if action in ('dismiss-card', 'lock-card', 'unlock-card'):
        target = resolve_file(cards, label)
        if action == 'dismiss-card':
            archived = move_archive(target, archive / '技术卡片')
            subject = key(target.stem)
            if subject not in policy['ignored_concepts']:
                policy['ignored_concepts'].append(subject)
            write_policy(state, policy)
            answer = f'技术卡片已归档并加入忽略表，不会自动再生成：{archived.name}'
        else:
            text = target.read_text(encoding='utf-8')
            target.write_text(set_locked(text, action == 'lock-card'), encoding='utf-8')
            answer = f'已{("锁定" if action == "lock-card" else "解锁")}技术卡片：{target.name}'
    elif action in ('dismiss-version', 'lock-version', 'unlock-version'):
        target = resolve_file(versions, label)
        if action == 'dismiss-version':
            body = target.read_text(encoding='utf-8')
            found = re.search(r'^source_after:\s*[\'\"]?([a-f0-9]{16,64})', body, re.I | re.M)
            if found and found.group(1)[:16] not in policy['dismissed_signatures']:
                policy['dismissed_signatures'].append(found.group(1)[:16])
            archived = move_archive(target, archive / '版本记录')
            write_policy(state, policy)
            # Pending enrichment will notice the original note is now missing.
            answer = f'版本笔记已归档，不会重建：{archived.name}'
        else:
            text = target.read_text(encoding='utf-8')
            target.write_text(set_locked(text, action == 'lock-version'), encoding='utf-8')
            answer = f'已{("锁定" if action == "lock-version" else "解锁")}版本笔记：{target.name}'
    elif action in ('ignore-topic', 'unignore-topic'):
        name = key(label)
        if not name:
            raise ValueError('技术主题不能留空')
        policy['ignored_concepts'] = [k for k in policy['ignored_concepts'] if key(k) != name]
        if action == 'ignore-topic':
            policy['ignored_concepts'].append(name)
        write_policy(state, policy)
        answer = f'已{("忽略" if action == "ignore-topic" else "恢复允许生成")}主题：{label}'
    else:
        raise ValueError('不支持的管理操作')
    if update_index:
        update_index()
    return answer


def card_destination(cards, proposed):
    """Reuse an existing alias card, avoiding a second RRF card in future."""
    match = key(proposed)
    candidates = [p for p in cards.glob('*.md') if key(p.stem) == match]
    if candidates:
        # Keep an existing canonical-ish RRF card, never silently merge/delete.
        candidates.sort(key=lambda p: (not p.name.startswith('RRF'), len(p.name), p.name))
        return candidates[0]
    return cards / (proposed + '.md')
