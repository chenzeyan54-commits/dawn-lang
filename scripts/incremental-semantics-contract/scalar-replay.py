#!/usr/bin/env python3
"""Exercise real scalar executor admission with compiling negative controls.

Unsupported bodies remain cold. These controls must reach the replay assertions;
parser, type-checker and JVM linkage failures do not establish their coverage.
"""
import re
import shutil
import tempfile
import time
from pathlib import Path

from cold import ROOT, edit, run


def main():
    started = time.monotonic()
    path = 'selfhost/src/check/scalar_replay.dawn'
    original = (ROOT / path).read_text()
    variants = [
        ('disable-replay', 'Some(p) -> candidate(p, cx, d, sig)', 'Some(p) -> None'),
        ('source-owner', 'not snapshot_matches(old, old_source)', 'false'),
        ('observer-mode', 'Some(_) -> moved.function_reads', 'Some(_) -> None'),
        ('current-isolation', 'isolated: cx.frame.isolated', 'isolated: false'),
        ('current-test-mode', 'in_test: cx.in_test', 'in_test: false'),
        ('current-handler-cell', 'take_cell: cx.take_cell', 'take_cell: None'),
        ('current-constant-cutoff', 'const_cutoff: cx.const_cutoff', 'const_cutoff: None'),
        ('current-loop-jumps', 'loop_jumps: cx.loop_jumps', 'loop_jumps: set.empty()'),
        # The declaration's own bytes. This is the whole pairing of the two
        # bodies now: identical text parses to the same tree, so admission
        # asks for the bytes rather than walking two parsed bodies.
        ('changed-declaration-text',
         'if not source_projection.same_text(prepared.old_tokens, prepared.tokens,\n'
         '    prior.lo, prior.hi, d.lo, d.hi) { return None }',
         'if false { return None }'),
        ('alias-shadowed-binders', 'if map.has(cx.module_aliases, name) { return None }',
         'if false { return None }'),
        # The record-time half of admission. Its verdicts are what the replay
        # path stops recomputing, so each one needs its own control.
        ('recorded-binders', 'let names = scalar_shape.binders(prior.body, prior_sig.param_names)?',
         'let names: List[String] = []'),
        ('header-only-admission',
         'let ids = allocation.body_relocation(prepared.headers, prior_sig, sig, no_evidence)?',
         'let ids = prepared.headers'),
        # The product is retained under a declaration key, so the key the
        # candidate declaration is looked up with has to be the one this
        # revision's own enumeration gives it. Reading the recorded revision's
        # positions, or treating a syntax index as an identity, both reach for
        # a product under a key that is not this declaration's.
        ('candidate-key-revision',
         'for located in identity.located(scope, next.syntax) {',
         'for located in identity.located(scope, source_snapshot.syntax(settled.source)) {'),
        ('candidate-key-index',
         'candidates = map.insert(candidates, located.span.lo, (located.entry.key, located.entry.declaration))',
         'candidates = map.insert(candidates, located.entry.declaration, (located.entry.key, located.entry.declaration))'),
        ('candidate-key-ignored',
         'let (key, declaration) = map.get(prepared.candidates, d.lo)?',
         'let (key, declaration) = map.values(prepared.candidates)[0]'),
    ]
    with tempfile.TemporaryDirectory(prefix='dawn-scalar-replay-') as temp:
        root = Path(temp)
        for directory in ('selfhost', 'compiler-plan'):
            shutil.copytree(ROOT / directory, root / directory,
                            ignore=shutil.ignore_patterns('build', '.dawn'))
        (root / 'packages').symlink_to(ROOT / 'packages', target_is_directory=True)
        for name, source in [('positive', original)] + [
                (name, edit(original, old, new)) for name, old, new in variants]:
            (root / path).write_text(source)
            status, output = run('test', root / path)
            if name == 'positive':
                if status or 'test(s) passed' not in output:
                    raise RuntimeError('Positive failed\n' + output)
            elif not status or not re.search(
                    r'^FAIL\s+check/scalar_replay :: scalar replay [^\n]*\n\s+assertion failed:',
                    output, re.M) or re.search(r'^error:', output, re.M):
                raise RuntimeError(name + ' missed its assertion owner\n' + output)
            print('OK: scalar replay ' + name, flush=True)
    print(f'OK: {len(variants)} compiling scalar replay controls, {time.monotonic() - started:.2f}s')


if __name__ == '__main__':
    main()
