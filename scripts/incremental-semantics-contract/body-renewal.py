#!/usr/bin/env python3
"""Require renewed products to survive real edits, not a second cold pass.

Each mutation must compile and reach a renewal assertion. Existing replay
tests alone cannot prove that products produced in this revision seed the next.
"""
import re
import shutil
import tempfile
import time
from pathlib import Path

from cold import ROOT, edit, run


SUBJECT = 'selfhost/src/check/scalar_replay.dawn'


def main():
    started = time.monotonic()
    original = (ROOT / SUBJECT).read_text()
    controls = [
        ('stale-generation', 'admitted: renewed,', 'admitted: admitted,'),
        ('discard-products', 'ready(data.scope, next, source, captured.entries)',
         'ready(data.scope, next, source, [])'),
        ('drop-reused-reads', 'Some(_) -> moved.function_reads', 'Some(_) -> Some([])'),
        ('drop-reused-writes', 'Some(_) -> moved.body_writes', 'Some(_) -> Some([])'),
        ('visits-as-cold-checks', 'visited: captured.visited, capture_refused: captured.capture_refused',
         'visited: outcome.counts.cold_unadmitted + outcome.counts.cold_rejected, capture_refused: captured.capture_refused'),
        ('cold-instead-of-replay',
         'let (outcome, captured) = body_execution.record_using(data.scope, next,\n'
         '    Pass { counts: zero_counts(), prepared: prepared }, pass_executor())',
         'let (outcome, captured) = body_execution.record_using(data.scope, next,\n'
         '    Pass { counts: zero_counts(), prepared: prepared }, checker.cold_body_executor())'),
    ]
    variants = [('positive', original)] + [
        (name, edit(original, old, new)) for name, old, new in controls]
    with tempfile.TemporaryDirectory(prefix='dawn-body-renewal-') as temp:
        root = Path(temp)
        for directory in ('selfhost', 'compiler-plan'):
            shutil.copytree(ROOT / directory, root / directory,
                            ignore=shutil.ignore_patterns('build', '.dawn'))
        (root / 'packages').symlink_to(ROOT / 'packages', target_is_directory=True)
        for name, source in variants:
            (root / SUBJECT).write_text(source)
            status, output = run('test', root / SUBJECT)
            if name == 'positive':
                if status or 'test(s) passed' not in output:
                    raise RuntimeError('Positive failed\n' + output)
            elif not status or not re.search(
                    r'^FAIL\s+check/scalar_replay :: scalar replay (?:renews|recovers renewal)[^\n]*\n\s+assertion failed:',
                    output, re.M) or re.search(r'^error:', output, re.M):
                raise RuntimeError(name + ' missed its compiling renewal assertion\n' + output)
            print('OK: body renewal ' + name, flush=True)
    print(f'OK: {len(controls)} compiling renewal controls, {time.monotonic() - started:.2f}s')


if __name__ == '__main__':
    main()
