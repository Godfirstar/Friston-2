"""Presentation helpers for ResearchJournal v3 (documentation only).

All displayed source code is extracted from the two actual snapshots. The model
may explain it, but cannot invent earlier source code. No project code is run.
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


def _fence(text):
    # Prevent markdown source snippets from prematurely terminating a fence.
    max_run = max((len(m.group()) for m in re.finditer(r'`+', text)), default=0)
    return '`' * max(3, max_run + 1)


def _snippet(lines, start, end, max_lines):
    """Strictly preserve a contiguous piece of a real snapshot."""
    start = max(0, start)
    end = min(len(lines), end)
    if end - start > max_lines:
        end = start + max_lines
    return '\n'.join(lines[start:end]), (start + 1, end)


def source_comparison(old_files, new_files, changed, max_files=3, max_hunks_per_file=2, max_lines=25):
    """Return deterministic before/after code excerpts, with grounded line ranges.

    No pseudo code: absent old files are clearly marked as new. To avoid enormous
    generated reports, large hunks are displayed with truncation notices.
    """
    scored = []
    for name in changed:
        before = old_files.get(name, '').splitlines()
        after = new_files.get(name, '').splitlines()
        opcodes = difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes()
        changes = [x for x in opcodes if x[0] != 'equal']
        score = sum(max(i2-i1, j2-j1) for _, i1, i2, j1, j2 in changes)
        if name.endswith(('.md', '.json', '.yml', '.yaml')):
            score *= 0.7
        scored.append((score, name, before, after, changes))
    scored.sort(reverse=True)
    items = []
    for _, name, before, after, ops in scored[:max_files]:
        for idx, (tag, i1, i2, j1, j2) in enumerate(ops[:max_hunks_per_file], 1):
            before_text, (b_start, b_end) = _snippet(before, i1-3, i2+3, max_lines)
            after_text, (a_start, a_end) = _snippet(after, j1-3, j2+3, max_lines)
            if not before_text and not after_text:
                continue
            items.append((name, idx, tag, (b_start, b_end), before_text,
                          (a_start, a_end), after_text,
                          ((i2-i1) > max_lines-6) or ((j2-j1) > max_lines-6)))
    if not items:
        return ''
    lines = [
        '## 修改前后源码（真实快照摘录）',
        '',
        '> [!important] 源码真实性与范围',
        '> 本节由程序直接从本轮**旧快照和新快照**摘取，不是模型编造的示例。',
        '> 只展示关键变化附近的局部代码；片段可能缺少上下文，不能作为完整可执行脚本。',
        '> 技术解释见前文「核心代码解读」，代码内的注释均为源码原有内容，不是 AI 擅加。',
        '',
    ]
    for name, idx, tag, b_range, b_text, a_range, a_text, truncated in items:
        lang = _LANGS.get(Path(name).suffix.lower(), 'text')
        lines.extend([f'### `{name}` · 改动片段 {idx}', '',
                      f'**旧版 · 第 {b_range[0]}–{b_range[1]} 行**' if b_text else '**旧版 · 不存在（本版新增）**', ''])
        if b_text:
            fence = _fence(b_text)
            lines.extend([f'{fence}{lang}', b_text, fence, ''])
        lines.extend([f'**新版 · 第 {a_range[0]}–{a_range[1]} 行**' if a_text else '**新版 · 已删除', ''])
        if a_text:
            fence = _fence(a_text)
            lines.extend([f'{fence}{lang}', a_text, fence, ''])
        changes = {'insert': '新增', 'delete': '删除', 'replace': '替换'}.get(tag, '修改')
        lines.extend([f'> **变更标注**：该片段属于「{changes}」；旧版 {b_range[0]}–{b_range[1]} 行对应新版 {a_range[0]}–{a_range[1]} 行。' if b_text and a_text
                      else f'> **变更标注**：该片段属于「{changes}」。', ''])
        if truncated:
            lines.extend(['> 此处改动较长，只显示前面的部分行；完整变化仍以本地源码快照为准。', ''])
    if len(scored) > max_files:
        lines.append(f'> 还有 {len(scored)-max_files} 个修改文件未在这里展开；文件清单见本篇末尾。')
    return '\n'.join(lines).strip()


def polish_markdown(text):
    """Apply conservative formatting repairs only to *new AI output*.

    Existing notes are never rewritten. Don't blindly transform arbitrary code.
    """
    text = text.strip().replace('\r\n', '\n')
    # A previously observed misuse: rendering the RRF formula as inline code.
    formula = r'\sum_{c\in C(d)}\frac{w_c}{K+\operatorname{rank}_c(d)}'
    for bad in ('`Σ 1/(60+rank)`', '`Σ 1 / (60 + rank)`'):
        text = text.replace(bad, '$' + formula + '$')
    return text
