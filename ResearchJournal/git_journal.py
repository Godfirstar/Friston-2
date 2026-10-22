#!/usr/bin/env python3
"""Git-commit-triggered documentation worker for Friston-2 ResearchJournal.

The authoritative input is the committed Git tree, NOT the working directory,
commit message, an idle timer, or the old feature baselines. Never executes
robot code or tests, and never creates Git commits or touches application files.

Commands:
    python git_journal.py enqueue <sha>  # very fast, called by post-commit hook
    python git_journal.py worker         # serial async worker + bounded retries
    python git_journal.py status         # read-only status
    python git_journal.py retry [sha|all] # resume failed tasks
"""
from __future__ import annotations

import argparse
import fnmatch
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import traceback

import journal as j
from milestone_guard import eligible

PENDING = j.STATE / 'git-queue' / 'pending'
DONE = j.STATE / 'git-queue' / 'done'
LOCKFILE = j.STATE / 'git-queue' / 'worker.lock'
MAX_ATTEMPTS = 3
# This is a bounded retry for committed work, not a periodic code scanner.
SHA_RE = re.compile(r'^[0-9a-f]{40,64}$')


def _git_env():
    env = os.environ.copy()
    # Git exports these variables to hooks; clear them so -C resolves this repository.
    for key in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_PREFIX',
                'GIT_COMMON_DIR', 'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES'):
        env.pop(key, None)
    return env


def git(*args, binary=False, max_output=None):
    exe = shutil.which('git')
    if not exe:
        raise RuntimeError('Git executable is missing in PATH')
    cmd = [exe, '-C', str(j.PROJECT), *args]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=45, check=False, env=_git_env())
    if proc.returncode:
        raise RuntimeError(('git ' + args[0] + ': ' + proc.stderr.decode('utf-8', 'replace')[-400:]).strip())
    if max_output is not None and len(proc.stdout) > max_output:
        raise RuntimeError('Git output exceeded configured size limit')
    return proc.stdout if binary else proc.stdout.decode('utf-8', 'replace').strip()


def repository_check():
    root = Path(git('rev-parse', '--show-toplevel')).resolve()
    if root != j.PROJECT.resolve():
        raise RuntimeError(f'ResearchJournal needs a Git repository rooted at {j.PROJECT}; found {root}')


def commit_id(ref):
    resolved = git('rev-parse', '--verify', f'{ref}^{{commit}}').lower()
    if not SHA_RE.fullmatch(resolved):
        raise RuntimeError('Invalid commit SHA')
    return resolved


def first_parent(commit):
    data = git('rev-list', '--parents', '-n', '1', commit).split()
    return data[1] if len(data) > 1 else ''


def modified_paths(commit, parent):
    if parent:
        data = git('diff', '--name-only', '-z', '--no-ext-diff', '--no-renames', parent, commit, '--', binary=True)
    else:
        data = git('ls-tree', '-r', '--name-only', '-z', commit, binary=True)
    return sorted({item.decode('utf-8', 'surrogateescape') for item in data.split(b'\x00') if item})


def _allowed(path, config):
    # The original strict allowlist excludes ResearchJournal, docs, external data, tests etc.
    if not eligible(path, config):
        return False
    patterns = [str(p).casefold() for p in config.get('excluded_name_patterns', [])]
    return not any(fnmatch.fnmatch(Path(path).name.casefold(), pattern) for pattern in patterns)


def _read_committed_file(revision, path, max_bytes):
    if not revision:
        return None
    spec = f'{revision}:{path}'
    size = subprocess.run([shutil.which('git'), '-C', str(j.PROJECT), 'cat-file', '-s', spec],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, env=_git_env())
    if size.returncode:
        return None  # Deleted/new path in this revision
    try:
        nbytes = int(size.stdout.strip())
    except (TypeError, ValueError):
        return None
    if nbytes > max_bytes:
        raise ValueError(f'{path} is {nbytes} bytes and exceeds max_file_bytes')
    body = git('show', spec, binary=True, max_output=max_bytes + 1024)
    if b'\x00' in body:
        raise ValueError(f'{path} has NUL/binary content')
    try:
        return body.decode('utf-8-sig')
    except UnicodeError as exc:
        raise ValueError(f'{path} is not UTF-8 source text') from exc


def source_pair(commit, config):
    parent = first_parent(commit)
    changed = modified_paths(commit, parent)
    filtered = [x for x in changed if _allowed(x, config)]
    file_cap = max(1, int(config.get('git_max_source_files', 30)))
    if len(filtered) > file_cap:
        raise ValueError(f'This commit contains {len(filtered)} eligible source files (limit {file_cap}); split the feature or explicitly raise git_max_source_files')
    old, new, ignored = {}, {}, []
    limit = int(config.get('max_file_bytes', 262144))
    for path in filtered:
        try:
            before = _read_committed_file(parent, path, limit)
            after = _read_committed_file(commit, path, limit)
        except ValueError as exc:
            ignored.append(str(exc))
            continue
        if before is not None:
            old[path] = before
        if after is not None:
            new[path] = after
    return parent, old, new, filtered, ignored


def _prepare_dirs():
    j.make_dirs()
    PENDING.mkdir(parents=True, exist_ok=True)
    DONE.mkdir(parents=True, exist_ok=True)


def _paths(commit):
    return PENDING / (commit + '.json'), DONE / (commit + '.json')


def enqueue(ref):
    repository_check()
    commit = commit_id(ref)
    _prepare_dirs()
    item, result = _paths(commit)
    if result.exists():
        return f'{commit[:12]} already processed'
    if item.exists():
        return f'{commit[:12]} already queued'
    payload = {'commit': commit, 'queued_at': j.time_text(), 'attempts': 0,
               'retry_after': 0, 'error': ''}
    # Atomic rename; don't alter any existing user files.
    j.save_json(item, payload)
    j.log(f'Git commit queued: {commit[:12]}')
    return f'{commit[:12]} queued'


def _existing_note(commit):
    needle = f"git_commit: '{commit}'"
    for path in sorted(j.VERSIONS.glob('*.md')):
        try:
            if needle in path.read_text(encoding='utf-8')[:1800]:
                return path
        except (OSError, UnicodeError):
            continue
    return None


def _done(commit, status, detail='', note=None):
    pending, finished = _paths(commit)
    j.save_json(finished, {'commit': commit, 'status': status, 'detail': detail,
                           'finished_at': j.time_text(), 'note': str(note or '')})
    pending.unlink(missing_ok=True)
    j.log(f'Git commit {commit[:12]}: {status}; {detail[:200]}')


def process_one(item, config):
    record = j.load_json(item, {})
    commit = record.get('commit', '')
    if not SHA_RE.fullmatch(commit) or item.name != commit + '.json':
        raise ValueError('Invalid queued commit identity')
    if (note := _existing_note(commit)) is not None:
        j.update_index()  # recover after a process interruption following note creation
        _done(commit, 'documented', 'Recovered existing note', note)
        return
    parent = first_parent(commit)
    if not parent and not config.get('git_summarize_initial_commit', False):
        _done(commit, 'baseline', 'Initial Git commit establishes history; no automatic giant baseline note')
        return
    parent, old, new, relevant, ignored = source_pair(commit, config)
    if ignored:
        j.log(f'Git {commit[:12]} excluded oversize/non-UTF8 files: {ignored[:8]}')
    if not relevant:
        _done(commit, 'skipped', 'No eligible robot source code in this commit')
        return
    if not j.changed_files(old, new):
        if ignored:
            _done(commit, 'skipped', 'Eligible files could not be safely read; see agent.log')
        else:
            _done(commit, 'skipped', 'No meaningful text source differences')
        return
    # Import explicit GPT exports if present. Historical discussions are weaker
    # than the actual version diff and never replace source-grounded conclusions.
    j.import_inbox(config)
    old_sig = j.sha('git-before:' + commit + '\x00' + j.signature(old))
    prev = {'signature': old_sig, 'files': old}
    cfg = dict(config)
    cfg['skip_trivial_notes'] = bool(config.get('git_skip_trivial_notes', True))
    j.log(f'Git analysis starting: {commit[:12]} ({len(relevant)} eligible source paths)')
    success = j.produce(prev, new, cfg, update_global_baseline=False,
                        scope_id='git:' + commit, allow_pending=False,
                        git_commit=commit, git_parent=parent)
    if not success:
        raise RuntimeError('Codex did not produce a usable report. Commit safely retained for retry.')
    note = _existing_note(commit)
    _done(commit, 'documented' if note else 'skipped-trivial',
          f'{len(relevant)} eligible paths, {len(ignored)} unreadable/oversize', note)


def _try_lock():
    LOCKFILE.parent.mkdir(parents=True, exist_ok=True)
    fd = LOCKFILE.open('a+b')
    fd.seek(0, 2)
    if fd.tell() == 0:
        fd.write(b'0')
        fd.flush()
    try:
        if os.name == 'nt':
            import msvcrt
            fd.seek(0)
            msvcrt.locking(fd.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fd.close()
        return None
    return fd


def worker():
    if j.load_config().get('trigger_mode') != 'git':
        return 'Git queue worker disabled: timer mode is active.'
    repository_check()
    _prepare_dirs()
    locked = _try_lock()
    if locked is None:
        return 'A Git journal worker is already running; queued changes remain safe.'
    j.log('Git journal worker started')
    config = j.load_config()
    try:
        # This loop ONLY retries explicitly queued Git commits, never checks
        # uncommitted files, idle time, or the milestone feature baseline.
        while True:
            if j.load_config().get('trigger_mode') != 'git':
                return 'Git worker is exiting because timer mode is active.'
            jobs = [j.load_json(p, {}) for p in PENDING.glob('*.json')]
            jobs = [x for x in jobs if isinstance(x, dict) and SHA_RE.fullmatch(x.get('commit', ''))]
            jobs.sort(key=lambda x: (x.get('queued_at', ''), x['commit']))
            active = [x for x in jobs if int(x.get('attempts', 0)) < MAX_ATTEMPTS]
            if not active:
                return 'No processable Git commits. Check status for any failed tasks.'
            now = time.time()
            due = next((x for x in active if float(x.get('retry_after') or 0) <= now), None)
            if due is None:
                wait = min(30, max(1, min(float(x['retry_after']) for x in active) - now))
                time.sleep(wait)
                continue
            commit = due['commit']
            jobpath, _ = _paths(commit)
            try:
                process_one(jobpath, config)
            except Exception as exc:
                latest = j.load_json(jobpath, due)
                count = int(latest.get('attempts', 0)) + 1
                latest['attempts'] = count
                latest['error'] = j.redact(str(exc))[:700]
                latest['retry_after'] = time.time() + int(config.get('retry_failed_after_seconds', 1800))
                j.save_json(jobpath, latest)
                j.log(f'Git commit {commit[:12]} analysis failed attempt {count}/{MAX_ATTEMPTS}: {exc}\n' + traceback.format_exc()[-1200:])
    finally:
        locked.close()
        j.log('Git journal worker stopped')


def retry(ref='all'):
    repository_check()
    _prepare_dirs()
    candidates = list(PENDING.glob('*.json'))
    if ref.lower() != 'all':
        commit = commit_id(ref)
        candidates = [p for p in candidates if p.stem == commit]
    changed = 0
    for path in candidates:
        record = j.load_json(path, {})
        if not record:
            continue
        record.update(attempts=0, retry_after=0, error='')
        j.save_json(path, record)
        changed += 1
    return f'Reset {changed} queued commit(s). Run worker to retry.'


def status():
    try:
        repository_check()
        root_state = f'Git repository: {j.PROJECT}'
        git_ok = True
    except Exception as exc:
        root_state = f'Git repository not ready: {exc}'
        git_ok = False
    _prepare_dirs()
    waiting = [j.load_json(path, {}) for path in PENDING.glob('*.json')]
    done = [j.load_json(path, {}) for path in DONE.glob('*.json')]
    lines = ['ResearchJournal v3.3 · Git-triggered mode', root_state,
             'Uncommitted working tree changes: NEVER analyzed',
             f'Queued commits: {len(waiting)}',
             f'Finished/skipped commits: {len(done)}']
    for record in sorted(waiting, key=lambda x: x.get('queued_at', ''))[-12:]:
        sha = str(record.get('commit',''))[:12]
        attempts = int(record.get('attempts',0))
        detail = str(record.get('error',''))[:160]
        state = 'needs manual retry' if attempts >= MAX_ATTEMPTS else 'queued/retrying'
        lines.extend([f'- {sha}: {state} ({attempts}/{MAX_ATTEMPTS})',
                      f'  last error: {detail or "none"}'])
    if done:
        final = sorted(done, key=lambda d: d.get('finished_at', ''))[-1]
        lines.append(f'Last completed: {str(final.get("commit", ""))[:12]} · {final.get("status", "unknown")}')
    if git_ok:
        lines.append(f'Current HEAD: {commit_id("HEAD")[:12]}')
    lines.append('Review trace: ResearchJournal/state/agent.log')
    lines.append('Run pending manually: python .\\ResearchJournal\\git_journal.py worker')
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description='Git-driven Friston-2 journal: no timer, no test execution')
    parser.add_argument('command', choices=('enqueue', 'worker', 'status', 'retry'))
    parser.add_argument('commit', nargs='?')
    args = parser.parse_args()
    if args.command == 'enqueue':
        if not args.commit:
            parser.error('enqueue requires a commit SHA')
        print(enqueue(args.commit))
    elif args.command == 'worker':
        print(worker())
    elif args.command == 'retry':
        print(retry(args.commit or 'all'))
    else:
        print(status())


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        j.log('Git worker interrupted')
        raise
    except Exception as exc:
        j.log('Git journal fatal: ' + repr(exc) + '\n' + traceback.format_exc()[-1400:])
        print('ResearchJournal error:', exc, file=sys.stderr)
        raise SystemExit(1)
