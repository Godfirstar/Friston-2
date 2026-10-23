#!/usr/bin/env python3
"""10-minute polling adapter for ResearchJournal v3.3's evidence-based note generator.

Reads only allowlisted project code; never runs the robot or generates tests.
The timer baseline is separate from the legacy/Git baselines so migration and rollback
never discard source history. No Git commit or Git hook is required.
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import os
from pathlib import Path
import time
import traceback

import journal as j

TIMER_BASELINE = j.STATE / 'timer-baseline.json.gz'
TIMER_STATE = j.STATE / 'timer-status.json'
DEFAULT_CODE_SUFFIXES = ('.py', '.pyi', '.c', '.cpp', '.h', '.hpp', '.js', '.jsx', '.ts', '.tsx', '.sql')
DEFAULT_INCLUDE_PATHS = ('friston3/', 'main.py', 'preview.py')


def _interval(config):
    try:
        return max(60, int(config.get('timer_interval_seconds', 600)))
    except (ValueError, TypeError):
        return 600


def _selection(config):
    raw_extensions = config.get('timer_source_extensions', list(DEFAULT_CODE_SUFFIXES))
    extensions = {str(s).strip().lower() for s in raw_extensions if isinstance(s, str)}
    raw_paths = config.get('timer_include_paths', list(DEFAULT_INCLUDE_PATHS))
    include = [str(s).replace('\\', '/').strip('/') for s in raw_paths if isinstance(s, str) and str(s).strip()]
    return extensions, include


def _in_scope(path, extensions, include):
    path = path.replace('\\', '/')
    if Path(path).suffix.lower() not in extensions:
        return False
    return any(path == prefix or path.startswith(prefix + '/') for prefix in include)


def select(files, config):
    extensions, include = _selection(config)
    return {path: text for path, text in files.items() if _in_scope(path, extensions, include)}


def read_timer_baseline():
    try:
        with gzip.open(TIMER_BASELINE, 'rt', encoding='utf-8') as stream:
            doc = json.load(stream)
        if isinstance(doc.get('files'), dict):
            return doc
    except (OSError, ValueError, TypeError):
        pass
    return None


def write_timer_baseline(files):
    TIMER_BASELINE.parent.mkdir(parents=True, exist_ok=True)
    record = {'signature': j.signature(files), 'updated': j.time_text(), 'files': files}
    tmp = TIMER_BASELINE.with_name(TIMER_BASELINE.name + '.tmp')
    with gzip.open(tmp, 'wt', encoding='utf-8') as stream:
        json.dump(record, stream, ensure_ascii=False)
    os.replace(tmp, TIMER_BASELINE)


def bootstrap(config):
    existing = read_timer_baseline()
    if existing:
        return existing
    legacy = j.read_baseline()
    if legacy is not None and isinstance(legacy.get('files'), dict):
        files = select(legacy['files'], config)
        reason = 'previous ResearchJournal source baseline (preserves unsummarized changes)'
    else:
        files = select(j.scan(config), config)
        reason = 'current source (no previous baseline exists)'
    write_timer_baseline(files)
    old = read_timer_baseline()
    j.log(f'Timer baseline initialized from {reason}: {len(files)} selected files')
    return old


def _seen_note(signature):
    # Recover a successful write followed by a crash before timer baseline advancement.
    key = "source_after: '" + signature[:16] + "'"
    for note in j.VERSIONS.glob('*.md'):
        try:
            beginning = note.read_text(encoding='utf-8')[:1800]
            if key in beginning and 'source_before:' in beginning:
                return note
        except (OSError, UnicodeError):
            continue
    return None


def run_once(config):
    """One read-only scan and optional generated documentation (no tests)."""
    j.make_dirs()
    if config.get('trigger_mode') != 'timer':
        raise RuntimeError('This command requires trigger_mode=timer in ResearchJournal/config.json')
    old = bootstrap(config)
    j.import_inbox(config)
    current = select(j.scan(config), config)
    old_package = sum(k.startswith('friston2/') for k in old['files'])
    new_package = sum(k.startswith('friston3/') for k in current)
    if old_package >= 3 and new_package >= 3 and not any(k.startswith('friston2/') for k in current):
        j.log('Detected stale Friston-2 timer baseline; run ResearchJournal/rename-fix/migrate_baseline.py (historical data will be archived).')
        return 'Old Friston-2 baseline needs migration; no AI summary created.'
    diff = j.changed_files(old['files'], current)
    status = j.load_json(TIMER_STATE, {})
    status['last_checked_at'] = j.time_text()
    status['changed_count'] = len(diff)
    status['changed_files'] = diff[:25]
    if not diff:
        status['last_result'] = 'unchanged'
        status.pop('last_error', None)
        j.save_json(TIMER_STATE, status)
        j.log('Timer scan: no changed eligible code; skipped.')
        return 'No source changes; no new note.'
    after = j.signature(current)
    recovered = _seen_note(after)
    if recovered:
        write_timer_baseline(current)
        status['last_result'] = 'recovered'
        status['last_note'] = str(recovered)
        j.save_json(TIMER_STATE, status)
        j.log(f'Timer scan recovered existing note for {after[:12]}: {recovered.name}')
        return f'Existing note recognized: {recovered.name}'
    if j.is_dismissed_version(j.STATE, after):
        write_timer_baseline(current)
        status['last_result'] = 'dismissed'
        j.save_json(TIMER_STATE, status)
        j.log('Timer scan skipped previously dismissed snapshot.')
        return 'Previously dismissed snapshot skipped.'
    try:
        # Reuse deterministic code excerpts, GPT context matching, technical cards,
        # source evidence and Codex configured model from journal.py.
        # The baseline advances only AFTER Codex successfully returns and the
        # note (or explicit trivial skip) is persisted. Never write fallback stubs.
        cfg = dict(config)
        cfg['skip_trivial_notes'] = bool(config.get('skip_trivial_notes', True))
        produced = j.produce({'files': old['files'], 'signature': j.signature(old['files'])},
                             current, cfg, update_global_baseline=False, allow_pending=False)
        if produced:
            write_timer_baseline(current)
            status['last_result'] = 'completed'
            status['last_success_at'] = j.time_text()
            status.pop('last_error', None)
            j.save_json(TIMER_STATE, status)
            j.log(f'Timer scan completed: {len(diff)} source file(s), signature {after[:12]}')
            return f'Change summary processed for {len(diff)} source file(s).'
        status['last_result'] = 'pending'
        status['last_error'] = 'Codex output was not completed; will retry at next interval.'
        j.save_json(TIMER_STATE, status)
        return 'Analysis deferred; unchanged baseline retained for retry.'
    except Exception as exc:
        status['last_result'] = 'failed'
        status['last_error'] = j.redact(str(exc))[:500]
        j.save_json(TIMER_STATE, status)
        j.log('Timer run failed: ' + repr(exc) + '\n' + traceback.format_exc()[-1500:])
        return f'Analysis failed and will retry: {str(exc)[:160]}'


def status(config):
    j.make_dirs()
    old = read_timer_baseline()
    state = j.load_json(TIMER_STATE, {})
    current = select(j.scan(config), config)
    extensions, include = _selection(config)
    changes = j.changed_files(old['files'], current) if old else []
    old_dirs = {p.split('/')[0] for p in old['files'] if p.startswith(('friston2/', 'friston3/'))} if old else set()
    new_dirs = {p.split('/')[0] for p in current if p.startswith(('friston2/', 'friston3/'))}
    lines = [
        'ResearchJournal v3.4 - 10-minute timer mode',
        'Resolved project root: ' + str(j.PROJECT),
        'Current source files in scope: ' + str(len(current)),
        'Baseline source packages: ' + (', '.join(sorted(old_dirs)) or 'none'),
        'Current source packages: ' + (', '.join(sorted(new_dirs)) or 'none'),
        f'Trigger: {config.get("trigger_mode", "unknown")}',
        f'Check period: {_interval(config)} seconds',
        'Git commits: not used as triggers',
        'Included code locations: ' + ', '.join(include),
        'Included extensions: ' + ', '.join(sorted(extensions)),
        f'Timer baseline exists: {old is not None}',
        f'Changed eligible files since timer baseline: {len(changes)}',
        'Latest scan: ' + str(state.get('last_checked_at') or 'not yet'),
        'Last result: ' + str(state.get('last_result') or 'not yet'),
        'Next scan: ' + str(state.get('next_scan_at') or 'watcher not started / unavailable'),
        'Watcher PID file: ' + str((j.STATE / 'watch.pid').read_text('ascii').strip()
                                   if (j.STATE / 'watch.pid').exists() else 'not found'),
        'Model: ' + ' / '.join(j.codex_model_info(config)),
    ]
    if 'friston2' in old_dirs and 'friston3' in new_dirs and 'friston2' not in new_dirs:
        lines.append('WARNING: baseline still refers to old package friston2/. Run rename migration before automatic analysis.')
    for filename in changes[:12]:
        lines.append('  - ' + filename)
    if state.get('last_error'):
        lines.append('Last error: ' + str(state['last_error'])[:300])
    lines.append('Journal log: ResearchJournal/state/agent.log')
    return '\n'.join(lines)


def watch(config):
    j.make_dirs()
    guard = j.single_instance()
    if guard is None:
        j.log('Timer watcher already running; another instance exits.')
        return 0
    pid_path = j.STATE / 'watch.pid'
    pid_path.write_text(str(os.getpid()), encoding='ascii')
    interval = _interval(config)
    j.log(f'Timer watcher started: interval={interval}s, project={j.PROJECT}, no Git hooks')
    try:
        bootstrap(config)
        deadline = time.monotonic() + interval  # Never auto-generate immediately during upgrade.
        while True:
            state = j.load_json(TIMER_STATE, {})
            state['next_scan_at'] = (j.now_local() + dt.timedelta(seconds=max(0, deadline-time.monotonic()))).isoformat(timespec='seconds')
            j.save_json(TIMER_STATE, state)
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(30, remaining))
                continue
            try:
                if j.load_config().get('trigger_mode') != 'timer':
                    j.log('Timer watcher stopped because trigger_mode changed.')
                    break
                result = run_once(j.load_config())
                j.log('Timer cycle: ' + result)
            except Exception as exc:
                j.log('Timer cycle error: ' + repr(exc) + '\n' + traceback.format_exc()[-1500:])
            deadline = time.monotonic() + interval
    except KeyboardInterrupt:
        j.log('Timer watcher stopped by user')
    finally:
        try:
            if pid_path.exists() and pid_path.read_text('ascii').strip() == str(os.getpid()):
                pid_path.unlink()
        except OSError:
            pass
        guard.close()
    return 0
