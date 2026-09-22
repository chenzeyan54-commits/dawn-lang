#!/usr/bin/env python3
"""Require import resolution to preserve the parsed source binding.

Private compiling subjects weaken one proof obligation at a time. A named
assertion must reject each mutation; panics and linkage failures do not count.
The original snapshot producer remains unchanged in every subject.
"""
import re
import shutil
import tempfile
import time
from pathlib import Path

from cold import ROOT, edit, run


def main():
    started = time.monotonic()
    original = (ROOT / 'selfhost/src/check/source_snapshot.dawn').read_text()
    start = original.index('pub fn resolved(')
    end = original.index('\n## The code-point index', start)
    body = original[start:end]
    variants = [
        ('accept-extra-declarations',
         'len(data.syntax.decls) != len(candidate.decls)',
         'len(data.syntax.decls) > len(candidate.decls)'),
        ('forget-declaration-order', 'index = index + 1', 'index = index'),
        ('ignore-non-import-declarations',
         '_ -> if original != changed { return None }', '_ -> ()'),
        ('accept-empty-import-path', 'path == [] || ', ''),
        ('keep-unresolved-syntax', 'syntax: candidate', 'syntax: data.syntax'),
        ('replace-source-index', 'syntax: candidate',
         'syntax: candidate, indexed: source_projection.index("")'),
        ('replace-source-tokens', 'syntax: candidate',
         'syntax: candidate, tokens: tokens(of("").expect("empty snapshot"))'),
    ]
    subjects = [('positive', original)]
    for name, old, new in variants:
        subjects.append((name, original[:start] + edit(body, old, new) + original[end:]))
    # Each import coordinate and selector has an independent rejection case.
    # Bind only the one candidate field being substituted into the expected
    # declaration, leaving the other field comparisons and fixtures intact.
    fields = ['selection', 'aname', 'lo', 'hi', 'nlo', 'nhi']
    for index, field in enumerate(fields):
        pattern = ['path'] + ['_'] * len(fields)
        pattern[index + 1] = 'candidate_field'
        changed = edit(body, 'DUseModule(path, _, _, _, _, _, _)',
                       'DUseModule(' + ', '.join(pattern) + ')')
        expected = ['path'] + fields
        expected[index + 1] = 'candidate_field'
        changed = edit(changed, 'changed != DUseModule(path, selection, aname, lo, hi, nlo, nhi)',
                       'changed != DUseModule(' + ', '.join(expected) + ')')
        subjects.append(('ignore-import-' + field, original[:start] + changed + original[end:]))
    owner = 'source snapshot resolution changes only import paths'
    failure = re.compile(r'^FAIL\s+check/source_snapshot :: ' + re.escape(owner) +
                         r'\n\s+assertion failed:', re.M)
    with tempfile.TemporaryDirectory(prefix='dawn-source-resolution-') as temp:
        root = Path(temp)
        for directory in ('selfhost', 'compiler-plan'):
            shutil.copytree(ROOT / directory, root / directory,
                            ignore=shutil.ignore_patterns('build', '.dawn'))
        (root / 'packages').symlink_to(ROOT / 'packages', target_is_directory=True)
        target = root / 'selfhost/src/check/source_snapshot.dawn'
        for name, source in subjects:
            target.write_text(source)
            before = time.monotonic()
            status, output = run('test', target)
            if name == 'positive':
                if status or 'test(s) passed' not in output:
                    raise RuntimeError('Positive failed\n' + output)
            elif not status or not failure.search(output) or re.search(r'^error:', output, re.M):
                raise RuntimeError(name + ' missed its assertion owner\n' + output)
            print(f'OK: source resolution {name}, {time.monotonic() - before:.2f}s', flush=True)
    print(f'OK: {len(subjects) - 1} compiling source resolution controls, '
          f'{time.monotonic() - started:.2f}s')


if __name__ == '__main__':
    main()
