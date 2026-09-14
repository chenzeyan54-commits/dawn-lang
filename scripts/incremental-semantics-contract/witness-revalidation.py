#!/usr/bin/env python3
"""Require canonical witness dependencies and candidate recomputation.

Each mutation must compile and fail its specific assertion owner. The private
subject preserves the workspace; this gate does not enable body caching.
"""
import re
import shutil
import tempfile
import time
from pathlib import Path

from cold import ROOT, edit, run


def main():
    started = time.monotonic()
    source = (ROOT / 'selfhost/src/check/checker.dawn').read_text()
    owner = 'witness revalidation recomputes candidate facts and refuses unknown queries'
    structural = 'witness reads preserve nominal recursion guards and substituted first gaps'
    variants = [
        ('accept-changed-answer', 'Some(semantic_reads.observed_equal(fact, observed.function_reads))',
         'Some(true)', owner),
        ('accept-missing-trait', 'if not map.has(initial.traits, id) { return Some(false) }',
         'if not map.has(initial.traits, id) { return Some(true) }', owner),
        ('ignore-candidate-owner', 'let initial = Cx { ..candidate, function_reads: Some([]) }',
         'let initial = Cx { ..candidate, owner_class: Some("m"), function_reads: Some([]) }',
         'unification revalidation checks candidate opacity and complete answers'),
        ('drop-recursion-guard-context', 'if set.has(seen, a.name) { return (cx, None) }',
         'if set.has(seen, a.name) { return (initial, None) }', structural),
        ('ignore-substituted-field', 'structural_gap_read(cx, tid, subst(f.ty, m, em), seen2)',
         'structural_gap_read(cx, tid, TyInt, seen2)', structural),
        ('drop-constructor-count-context', 'cx = count_cx', 'cx = cx', structural),
        ('invert-concreteness', 'semantic_reads.ConcreteType(t, answer)',
         'semantic_reads.ConcreteType(t, not answer)',
         'concreteness reads revalidate rigid parameter scope changes'),
        ('swap-compatibility-operands', 'semantic_reads.AssignableType(t, target, answer)',
         'semantic_reads.AssignableType(target, t, answer)',
         'assignability reads revalidate opaque visibility in either direction'),
        ('drop-inferred-types', 'after: InferenceBindings { types: map.entries(types), effects: map.entries(effects) }',
         'after: InferenceBindings { types: [], effects: map.entries(effects) }',
         'unification reads retain complete bindings on success and partial failure'),
        ('drop-effect-reduction-bindings', 'semantic_reads.ReducedEffect(e, map.entries(m), answer)',
         'semantic_reads.ReducedEffect(e, [], answer)',
         'effect reduction reads retain bindings and candidate associated rows'),
        ('drop-type-reduction-bindings', 'semantic_reads.ReducedTypeEffects(t, map.entries(m), answer)',
         'semantic_reads.ReducedTypeEffects(t, [], answer)',
         'effect reduction reads retain bindings and candidate associated rows'),
    ]
    subjects = [('positive', 'checker', source, None)] + [
        (name, 'checker', edit(source, old, new), test) for name, old, new, test in variants
    ]
    # Twenty projection controls stood here and are gone with the read-log
    # projection they mutated (K7b). Fourteen were generated per constructor
    # arm of `project_with_inference` (`project-ImplementationPresence-0`
    # through `project-ReducedTypeEffects-13`) and six were written out:
    # `project-dictionary-local`, `project-inference-type-key`,
    # `project-inference-effect-key`, `project-inference-type-answer`,
    # `project-inference-effect-answer` and `project-unification-after`.
    # Each replaced one domain's callback with another domain's, which is a
    # mutation only while there is a per-arm rebuild to confuse. A recorded
    # fact is carried whole now, and the one reference that still moves is the
    # alias source, whose controls live in `type-reads.py`. What the checker
    # still has to get right is above: which facts it recomputes and how it
    # compares them.
    with tempfile.TemporaryDirectory(prefix='dawn-witness-revalidation-') as temp:
        root = Path(temp)
        for directory in ('selfhost', 'compiler-plan'):
            shutil.copytree(ROOT / directory, root / directory,
                            ignore=shutil.ignore_patterns('build', '.dawn'))
        (root / 'packages').symlink_to(ROOT / 'packages', target_is_directory=True)
        for name, module, text, test in subjects:
            # Restore the module so each negative has exactly one mutation.
            (root / 'selfhost/src/check/checker.dawn').write_text(source)
            target = root / f'selfhost/src/check/{module}.dawn'
            target.write_text(text)
            status, output = run('test', target)
            if test is None:
                if status or 'test(s) passed' not in output:
                    raise RuntimeError('Positive failed\n' + output)
            else:
                failure = re.compile(r'^FAIL\s+check/' + module + r' :: ' + re.escape(test) +
                                     r'\n\s+assertion failed:', re.M)
                if not status or not failure.search(output) or re.search(r'^error:', output, re.M):
                    raise RuntimeError(name + ' did not compile and reach its owner\n' + output)
            print('OK: witness revalidation ' + name, flush=True)
    print(f'OK: {len(variants)} compiling witness controls, '
          f'{time.monotonic() - started:.2f}s')


if __name__ == '__main__':
    main()
