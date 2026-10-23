#!/usr/bin/env python3
"""One-time migration for Friston-2 -> Friston-3 timer baselines.

Only metadata/baseline files are changed; never runs code/tests or calls Codex.
A complete copy of the original timer baseline is retained for historical recovery.
"""
from __future__ import annotations
import datetime as dt
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sys

HOME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HOME))
import journal as j
import timer_journal as t


def normalize_names(text):
    # Detect rename-only changes without altering or saving any original source text.
    replacements = (
        ('friston2', 'friston3'),
        ('Friston2', 'Friston3'),
        ('FRISTON2', 'FRISTON3'),
        ('Friston-2', 'Friston-3'),
        ('FRISTON-2', 'FRISTON-3'),
        ('friston-2', 'friston-3'),
    )
    for a, b in replacements:
        text = text.replace(a, b)
    return text


def main():
    config = j.load_config()
    if config.get('trigger_mode') != 'timer':
        raise SystemExit('Not in timer mode; no baseline migrated.')
    old = t.read_timer_baseline()
    if old is None:
        print('No old timer baseline exists. The watcher will create a fresh baseline.'); return 0
    previous = old['files']
    current = t.select(j.scan(config), config)
    old_pkg = [k for k in previous if k.startswith('friston2/')]
    new_pkg = [k for k in current if k.startswith('friston3/')]
    old_live = [k for k in current if k.startswith('friston2/')]
    matched = [k for k in old_pkg if 'friston3/' + k[len('friston2/'):] in current]
    print(f'Old snapshot: {len(previous)} files, including {len(old_pkg)} friston2/ files')
    print(f'Current scan: {len(current)} files, including {len(new_pkg)} friston3/ files')
    if not old_pkg:
        print('Old timer baseline already uses the current project path; nothing to migrate.')
        return 0
    if len(old_pkg) < 3 or len(new_pkg) < 3 or old_live or len(matched) < max(3, int(len(old_pkg) * .65)):
        raise SystemExit('Rename pattern not confidently detected; baseline left unchanged. Check config and project location.')
    old_by_new = {
        ('friston3/' + key[len('friston2/'):] if key.startswith('friston2/') else key): value
        for key, value in previous.items()
    }
    changes = []
    for filename in sorted(set(old_by_new) | set(current)):
        b, a = old_by_new.get(filename), current.get(filename)
        if b is None or a is None or normalize_names(b) != normalize_names(a):
            changes.append({
                'file': filename,
                'change': 'added' if b is None else ('removed' if a is None else 'content_changed'),
                'old_hash': hashlib.sha256(b.encode('utf-8')).hexdigest()[:12] if b is not None else None,
                'new_hash': hashlib.sha256(a.encode('utf-8')).hexdigest()[:12] if a is not None else None,
            })
    migration_dir = j.STATE / 'rename-migration'
    migration_dir.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now().strftime('%Y%m%d-%H%M%S')
    saved = migration_dir / f'timer-baseline-Friston2-before-{timestamp}.json.gz'
    shutil.copy2(t.TIMER_BASELINE, saved)
    report = migration_dir / f'migration-{timestamp}.json'
    report.write_text(json.dumps({
        'message': 'Historical baseline preserved. A new Friston-3 monitoring baseline was established. Unpublished changes since old baseline were NOT summarized.',
        'previous_timer_baseline': str(saved),
        'project_path': str(j.PROJECT),
        'old_package': 'friston2/',
        'new_package': 'friston3/',
        'rename_matches': len(matched),
        'substantive_or_unmatched_files': changes,
        'previous_baseline_sha': old.get('signature'),
        'current_signature': j.signature(current),
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    t.write_timer_baseline(current)
    # Clear stale schedule/status copied from the previous project folder.
    stat = j.load_json(t.TIMER_STATE, {})
    stat['last_result'] = 'migrated_fr2_to_fr3'
    stat['last_checked_at'] = j.time_text()
    stat['changed_count'] = 0
    stat['changed_files'] = []
    stat['migration_report'] = str(report)
    stat.pop('next_scan_at', None)
    stat.pop('last_error', None)
    j.save_json(t.TIMER_STATE, stat)
    j.log('Friston-2 to Friston-3 migration: saved old timer baseline; new timer baseline established. Historical changes requiring review: ' + str(len(changes)))
    print('Migration complete, old baseline protected:', saved)
    print('Migration report:', report)
    print('Previously unreported changed/new/removed files:', len(changes))
    print('Future changes to Friston-3 code will be detected in subsequent timer cycles.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
