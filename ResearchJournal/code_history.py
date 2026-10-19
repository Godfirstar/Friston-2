"""Local, immutable source history and honest historical snippet reconstruction.

Documentation-only. This module NEVER runs project source or git commands.
"""
from __future__ import annotations

import gzip
import json
import os
from pathlib import Path
import re


def archive_snapshot(state: Path, files: dict[str, str], signature: str) -> Path:
    folder = state / 'snapshots'
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (signature + '.json.gz')
    if target.exists():
        return target
    # Atomic, append-only snapshot storage: never replace previous history.
    temporary = folder / (signature + f'.{os.getpid()}.tmp')
    try:
        with gzip.open(temporary, 'wt', encoding='utf-8') as handle:
            json.dump({'signature': signature, 'files': files}, handle, ensure_ascii=False)
        if not target.exists():
            # The watcher uses a single-instance lock; this protects most concurrent writes.
            os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def find_snapshot(state: Path, prefix: str):
    prefix = (prefix or '').lower().strip()
    if not re.fullmatch(r'[0-9a-f]{12,64}', prefix):
        return None
    candidates = list((state / 'snapshots').glob(prefix + '*.json.gz')) if (state / 'snapshots').exists() else []
    current = state / 'baseline.json.gz'
    if current.exists():
        candidates.append(current)
    for path in candidates:
        try:
            with gzip.open(path, 'rt', encoding='utf-8') as handle:
                record = json.load(handle)
            if (isinstance(record, dict)
                    and str(record.get('signature', '')).lower().startswith(prefix)
                    and isinstance(record.get('files'), dict)):
                return record
        except (OSError, ValueError, TypeError):
            continue
    return None


def version_ids(note: str):
    head = re.match(r'\A---\s*\n(.*?)\n---\s*\n', note, re.S)
    if not head:
        return None, None
    def read(key):
        match = re.search(r'^' + key + r'\s*:\s*[\'\"]?([0-9a-f]{12,64})', head.group(1), re.I | re.M)
        return match.group(1) if match else None
    return read('source_before'), read('source_after')


def referenced_source_paths(note: str, files: dict[str, str]):
    # Prefer filenames explicitly listed in the objective changes section.
    names = []
    lower = note.lower()
    for name in files:
        if ('`' + name.lower() + '`') in lower and name not in names:
            names.append(name)
    return names[:4]


def _importance(line):
    s = line.strip()
    if not s or s.startswith('#'):
        return 0
    weights = [('RRF', 10), ('semantic_weight', 10), ('channel_weight', 9),
               ('tokenize(', 9), ('if not ids', 9), ('dense', 5), ('lexical', 5),
               ('rank', 5), ('score', 4), ('detect_intent', 6), ('return ', 2),
               ('def ', 2), ('if ', 2), ('for ', 1)]
    return sum(weight for key, weight in weights if key.lower() in s.lower())


def _note_for_line(text):
    s = text.strip()
    if 'RRF_K +' in s or '(RRF_K+' in s:
        return '这里按“通道权重 ÷（平滑常数 + 名次）”累计融合分数；并非直接混合原始相似度。'
    if 'semantic_weight' in s:
        return '设置语义通道的贡献权重；权重本身不是效果提升的实测证据。'
    if 'tokenize(' in s:
        return '将文字拆成检索词项；如果再转成 set，重复词项会被去重。'
    if 'not ids' in s:
        return '仅当上游没有解析出实体 ID 时成立；不等于问题实际上没有涉及人物。'
    if s.startswith('if ') or s.startswith('elif '):
        return '分支条件；理解该行时需要检查条件不成立时走向哪里。'
    if s.startswith('return '):
        return '结束当前函数并返回结果；要注意返回值供哪些调用方使用。'
    if s.startswith('for '):
        return '逐个处理候选；循环中是否改变名次或过滤数量是关键。'
    if s.startswith('def '):
        return '函数入口；先明确参数来源与返回值契约。'
    if re.search(r'\b(score|weight|rank)\b', s):
        return '与评分/排序相关，注意是否改变候选顺序或下游分数字段含义。'
    if '=' in s:
        return '此行更新了变量或配置值；应结合后续使用点判断影响。'
    return '本行位于本次变化附近，结合完整函数上下文理解。'


def latest_only_excerpt(files: dict[str, str], names: list[str], max_files=2, max_blocks=2, max_lines=20):
    """Show actual new-version code when no old snapshot exists; never mislabel it as diff."""
    from note_format import fenced_source
    names = [name for name in names if name in files][:max_files]
    if not names:
        return ''
    body = [
        '## 历史代码补充（仅找到本版源码）', '',
        '> [!warning] 没有找到此记录对应的**旧快照**。以下代码来自与笔记 `source_after`',
        '> 一致的真实源码快照，**不是修改前后对照**。不能据此还原旧实现。', '',
    ]
    for name in names:
        lines = files[name].splitlines()
        if not lines:
            continue
        size = min(max_lines, len(lines))
        candidates = []
        for begin in range(0, len(lines)-size+1):
            end = begin + size
            candidates.append((sum(_importance(s) for s in lines[begin:end]), begin, end))
        candidates.sort(key=lambda x: (-x[0], x[1]))
        chosen = []
        for rating, begin, end in candidates:
            if all(end <= a or begin >= b for a, b in chosen):
                chosen.append((begin, end))
                if len(chosen) >= max_blocks:
                    break
        windows = [(a, b, 0) for a, b in sorted(chosen)]
        if not windows:
            continue
        body.extend([f'### `{name}`', ''])
        for start, end, _ in windows:
            excerpt = '\n'.join(lines[start:end])
            body.extend([f'**本版实际源码：L{start+1}–L{end}**', '', fenced_source(name, excerpt), '',
                         '**逐行阅读注释（额外解释，不修改原代码）**', ''])
            focus = sorted(range(start, end), key=lambda n: _importance(lines[n]), reverse=True)[:3]
            for i in sorted(focus):
                code = lines[i].strip()
                if code:
                    body.append(f'- `L{i+1}` `{code[:100].replace("`", "")}`：{_note_for_line(code)}')
            body.append('')
    return '\n'.join(body).strip()
