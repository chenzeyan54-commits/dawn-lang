#!/usr/bin/env python3
"""Exercise real scalar executor admission with compiling negative controls.

Unsupported bodies remain cold. These controls must reach the replay assertions;
parser, type-checker and JVM linkage failures do not establish their coverage.

Most controls mutate the replay module itself. Two mutate the scheduler and the
diagnostic sink it shares with the cold path, because the assembly order and the
diagnostic order are the shared scheduler's and cannot be broken from inside the
replay module: a control for either one has to reach where the order is made,
and the assertion it reddens is still a replay assertion.
"""
import re
import shutil
import tempfile
import time
from pathlib import Path

from cold import ROOT, edit, run


SUBJECT = 'selfhost/src/check/scalar_replay.dawn'
SCHEDULER = 'selfhost/src/check/checker.dawn'
CONTEXT = 'selfhost/src/check/cx.dawn'


def main():
    started = time.monotonic()
    own = [
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
        # Admission against the recorded header rather than the candidate
        # one. What this turns off is the refusal: a declaration whose own
        # bytes are unchanged can still have a different signature, because
        # the types it names are declared elsewhere.
        ('header-only-admission',
         'if not allocation.body_relocation(prior_sig, sig, no_evidence) { return None }',
         'if not allocation.body_relocation(prior_sig, prior_sig, no_evidence) { return None }'),
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
        # The cold remainder, one control per declaration category. Five of
        # the executor's six roles go straight to the cold executor and the
        # sixth goes there when admission refuses, so a category that stops
        # being counted is a category that left the remainder silently.
        ('cold-remainder-function',
         '        }\n'
         '        None -> cold.function(Counts { ..count, checked: count.checked + 1 }, cx, d, sig)',
         '        }\n        None -> cold.function(count, cx, d, sig)'),
        ('cold-remainder-inferred-body',
         'inferred_body: (n, cx, d, sig) => cold.inferred_body(Counts { ..n, checked: n.checked + 1 }, cx, d, sig),',
         'inferred_body: (n, cx, d, sig) => cold.inferred_body(n, cx, d, sig),'),
        ('cold-remainder-constant',
         'constant: (n, cx, d, ty, visible) => cold.constant(Counts { ..n, checked: n.checked + 1 }, cx, d, ty, visible),',
         'constant: (n, cx, d, ty, visible) => cold.constant(n, cx, d, ty, visible),'),
        ('cold-remainder-method',
         'method: (n, cx, tr, subject, d, sig) => cold.method(Counts { ..n, checked: n.checked + 1 }, cx, tr, subject, d, sig),',
         'method: (n, cx, tr, subject, d, sig) => cold.method(n, cx, tr, subject, d, sig),'),
        ('cold-remainder-default-body',
         'default_body: (n, cx, tr, d, sig, body) => cold.default_body(Counts { ..n, checked: n.checked + 1 }, cx, tr, d, sig, body),',
         'default_body: (n, cx, tr, d, sig, body) => cold.default_body(n, cx, tr, d, sig, body),'),
        ('cold-remainder-test-body',
         'test_body: (n, cx, name, body) => cold.test_body(Counts { ..n, checked: n.checked + 1 }, cx, name, body)',
         'test_body: (n, cx, name, body) => cold.test_body(n, cx, name, body)'),
    ]
    # The assembly boundary is not this module's to break: the order the
    # declarations come out in and the order their diagnostics come out in are
    # both the shared scheduler's, which is the whole reason replay reuses it
    # rather than assembling a module of its own. A control for either one has
    # to mutate the scheduler, and the assertion it has to redden is here.
    shared = [
        ('assembly-order', SCHEDULER,
         '      tfuns = tfuns ++ [Some(tast_positions.function(owner.resolver, tf))]',
         '      tfuns = [Some(tast_positions.function(owner.resolver, tf))] ++ tfuns'),
        ('diagnostic-order', CONTEXT,
         '  Cx { ..cx, diags: cx.diags ++ [raised(cx, msg, lo, hi, "")] }',
         '  Cx { ..cx, diags: [raised(cx, msg, lo, hi, "")] ++ cx.diags }'),
    ]
    variants = [(name, SUBJECT, old, new) for name, old, new in own] + shared
    originals = {p: (ROOT / p).read_text() for p in {v[1] for v in variants}}
    with tempfile.TemporaryDirectory(prefix='dawn-scalar-replay-') as temp:
        root = Path(temp)
        for directory in ('selfhost', 'compiler-plan'):
            shutil.copytree(ROOT / directory, root / directory,
                            ignore=shutil.ignore_patterns('build', '.dawn'))
        (root / 'packages').symlink_to(ROOT / 'packages', target_is_directory=True)
        for name, target, source in [('positive', SUBJECT, originals[SUBJECT])] + [
                (name, target, edit(originals[target], old, new))
                for name, target, old, new in variants]:
            for other, text in originals.items():
                (root / other).write_text(text)
            (root / target).write_text(source)
            status, output = run('test', root / SUBJECT)
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
