"""Obsidian-friendly, provenance-safe source snippets for ResearchJournal v3.1.

Extracts only actual before/after snapshot lines and attaches separate reader notes.
No AI may change the source code blocks, and this module never executes them.
"""
from __future__ import annotations

import difflib
from pathlib import Path
import re

_LANGS = {
    '.py': 'python', '.ps1': 'powershell', '.psm1': 'powershell',
    '.sh': 'bash', '.js': 'javascript', '.ts': 'typescript', '.tsx': 'tsx',
    '.json': 'json', '.yml': 'yaml', '.yaml': 'yaml', '.toml': 'toml',
    '.sql': 'sql', '.c': 'c', '.cpp': 'cpp', '.h': 'c', '.hpp': 'cpp',
    '.md': 'markdown',
}


def fenced_source(filename: str, text: str):
    # Use fences longer than any literal backtick run inside the source.
    runs = [len(m.group()) for m in re.finditer(r'`+', text)]
    fence = '`' * max(3, max(runs, default=0) + 1)
    lang = _LANGS.get(Path(filename).suffix.lower(), 'text')
    return f'{fence}{lang}\n{text}\n{fence}'


def _importance(line: str):
    s = line.strip().lower()
    if not s or s.startswith('#'):
        return 0
    tokens = [('rrf', 8), ('semantic_weight', 11), ('channel_weight', 8),
              ('tokenize(', 8), ('if not ids', 10), ('detect_intent', 9),
              ('dense', 4), ('lexical', 4), ('weight', 6), ('score', 4),
              ('rank', 4), ('if ', 2), ('return', 2), ('def ', 2)]
    return sum(v for k, v in tokens if k in s)


def _best_window(lines, start, end, budget):
    """Choose the most informative contiguous real source span inside a large hunk."""
    start, end = max(0, start), min(len(lines), end)
    if start >= end:
        return start, start
    if end - start <= budget:
        return start, end
    best_score, best_start = -1, start
    for begin in range(start, end - budget + 1):
        rating = sum(_importance(x) for x in lines[begin:begin+budget])
        if rating > best_score:
            best_score, best_start = rating, begin
    return best_start, best_start + budget


def annotation_for_line(text: str):
    s = text.strip()
    if not s:
        return '空行或格式分隔。'
    if 'RRF_K +' in s or '(RRF_K+' in s:
        return '按排名贡献计算融合得分；分母包含平滑常数和候选名次。'
    if 'semantic_weight' in s or 'channel_weight' in s:
        return '改变某个检索通道对最终排序的贡献，不等于提高了模型自身准确率。'
    if 'tokenize(' in s:
        return '调用分词/词项处理；需要看调用前后是否去重，以及用什么条件过滤。'
    if 'not ids' in s:
        return '这里判断的是“实体 ID 没被解析出来”，并非断言问题与任何实体无关。'
    if s.startswith(('if ', 'elif ')):
        return '进入此分支前必须满足条件；比较旧版时注意分支是否改变。'
    if s.startswith('return '):
        return '函数在这里结束并返回结果；看看下游如何使用这个结果。'
    if s.startswith('for '):
        return '逐个遍历候选；留意循环内筛选或分数累加的先后次序。'
    if s.startswith('def '):
        return '函数入口；理解其参数、调用方与返回契约。'
    if re.search(r'\b(score|weight|rank)\b', s):
        return '排序/评分计算点；可能改变结果顺序或分数字段含义。'
    if '=' in s:
        return '此处改变了数据或阈值；追踪变量下游的用途。'
    return '这行属于本次变更点；请结合前后几行确认含义。'


def _annotations(before, after, opcode, max_items=4):
    tag, i1, i2, j1, j2 = opcode
    additions = sorted(range(j1, j2), key=lambda p: _importance(after[p]), reverse=True)
    # Changed new lines are the strongest anchors. For deletions, use actual old lines.
    focus = [('新', n+1, after[n]) for n in additions[:max_items]]
    if not focus and i1 < i2:
        focus = [('旧', n+1, before[n]) for n in range(i1, min(i2, i1+max_items))]
    lines = []
    for kind, lineno, raw in focus:
        text = raw.strip()
        if not text:
            continue
        # Keep the literal text safe inside inline-code using escaping, and limit line length.
        quoted = text[:110].replace('`', "'")
        lines.append(f'- **{kind}版 L{lineno}** `{quoted}`：{annotation_for_line(text)}')
    return lines


def source_comparison(old_files, new_files, changed, max_files=3,
                      max_hunks_per_file=3, max_lines=35):
    """Return authentic before/after excerpts. No invented code or synthetic comments."""
    scored_files = []
    for name in changed:
        old_lines = old_files.get(name, '').splitlines()
        new_lines = new_files.get(name, '').splitlines()
        diffs = [op for op in difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False).get_opcodes()
                 if op[0] != 'equal']
        if not diffs:
            continue
        rating = sum(min(max(i2-i1, j2-j1), 70) + sum(_importance(x) for x in new_lines[j1:j2])
                     for _, i1, i2, j1, j2 in diffs)
        scored_files.append((rating, name, old_lines, new_lines, diffs))
    scored_files.sort(reverse=True)
    if not scored_files:
        return ''
    out = [
        '## 修改前后源码（真实快照 + 中文阅读注释）', '',
        '> [!important] 代码真实性',
        '> 所有代码块直接截取本地旧/新源码快照。**代码块原样不加注释，中文解读单独放在下面**，以免误把 AI 注释当成项目源代码。',
        '> 内容是局部片段；对照的是相邻源码快照，不等于已经手动运行或验证。', '',
    ]
    for _, name, before, after, diffs in scored_files[:max_files]:
        diffs = sorted(diffs, key=lambda op: sum(_importance(x) for x in after[op[3]:op[4]]) +
                       min(max(op[2]-op[1], op[4]-op[3]), 40), reverse=True)
        selected = sorted(diffs[:max_hunks_per_file], key=lambda op: (op[1], op[3]))
        out.extend([f'### `{name}`', ''])
        if name not in old_files:
            out.extend(['> 旧快照中不存在同名文件（本次新增）；这**不代表**项目过去没有其他实现同一功能的模块。', ''])
        for index, op in enumerate(selected, 1):
            tag, i1, i2, j1, j2 = op
            before_start, before_end = _best_window(before, i1-3, i2+3, max_lines)
            after_start, after_end = _best_window(after, j1-3, j2+3, max_lines)
            b = '\n'.join(before[before_start:before_end])
            a = '\n'.join(after[after_start:after_end])
            if not b and not a:
                continue
            out.extend([f'**片段 {index} · {dict(insert="新增", delete="删除", replace="替换").get(tag,"修改")}**', ''])
            if b:
                out.extend([f'**修改前 · L{before_start+1}–L{before_end}**', '', fenced_source(name, b), ''])
            else:
                out.extend(['**修改前：此处没有对应代码**', ''])
            if a:
                out.extend([f'**修改后 · L{after_start+1}–L{after_end}**', '', fenced_source(name, a), ''])
            else:
                out.extend(['**修改后：该部分已删除**', ''])
            annotations = _annotations(before, after, op)
            if annotations:
                out.extend(['**逐行阅读注释（不属于源代码）**', '', *annotations, ''])
            if (i2-i1) > max_lines or (j2-j1) > max_lines:
                out.extend(['> 此处改动较长，只摘取了原始代码中的代表性连续片段；其余内容未展示。', ''])
    if len(scored_files) > max_files:
        out.append(f'> 还有 {len(scored_files) - max_files} 个变化文件未展开；请查看文末完整变更清单。')
    return '\n'.join(out).strip()


def insert_comparison(markdown: str, comparison: str):
    """Put the mandatory code block beside the conceptual explanation, not at the bottom."""
    if not comparison:
        return markdown
    heading = re.search(r'(?m)^## 关键代码解读\s*$', markdown)
    if not heading:
        return markdown.rstrip() + '\n\n' + comparison
    tail = re.search(r'(?m)^## (?!修改前后源码)[^\n]+$', markdown[heading.end():])
    position = heading.end() + tail.start() if tail else len(markdown)
    return markdown[:position].rstrip() + '\n\n' + comparison + '\n\n' + markdown[position:].lstrip('\n')


def polish_markdown(text):
    """Fix a few known Markdown mistakes in *new model output only*."""
    text = text.strip().replace('\r\n', '\n')
    formula = r'\sum_{c\in C(d)}\frac{w_c}{K+\operatorname{rank}_c(d)}'
    for bad in ('`Σ 1/(60+rank)`', '`Σ 1 / (60 + rank)`'):
        text = text.replace(bad, '$' + formula + '$')
    return text
