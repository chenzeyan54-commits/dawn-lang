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
        ('discard-products', 'ready(data.scope, next, source, captured.entries, captured.bodies.cx)',
         'ready(data.scope, next, source, [], captured.bodies.cx)'),
        ('drop-reused-reads', 'Some(_) -> moved.function_reads', 'Some(_) -> Some([])'),
        ('drop-reused-writes', 'Some(_) -> moved.body_writes', 'Some(_) -> Some([])'),
        ('visits-as-cold-checks', 'visited: captured.visited, capture_refused: captured.capture_refused',
         'visited: outcome.counts.cold_unadmitted + outcome.counts.cold_rejected, capture_refused: captured.capture_refused'),
        ('cold-instead-of-replay',
         'let (outcome, captured) = body_execution.record_replaying(data.scope, next,\n'
         '    Pass { counts: zero_counts(), prepared: prepared }, renewal_decisions(), pass_executor())',
         'let (outcome, captured) = body_execution.record_using(data.scope, next,\n'
         '    Pass { counts: zero_counts(), prepared: prepared }, checker.cold_body_executor())'),
    ]
    variants = [('positive', original)] + [
        (name, edit(original, old, new)) for name, old, new in controls]
    product_path = 'selfhost/src/check/body_product.dawn'
    execution_path = 'selfhost/src/check/body_execution.dawn'
    originals = {SUBJECT: original,
                 product_path: (ROOT / product_path).read_text(),
                 execution_path: (ROOT / execution_path).read_text()}
    certified_controls = [
        ('unchecked-callback-environment', product_path,
         'if not environment_unchanged(before, after, tracked) { return None }',
         'if false { return None }',
         'check/body_execution :: renewal decisions preserve strict callbacks and single assembly output'),
        ('unbound-installation-output', product_path,
         'InstallationData { cx: after, tree: product.tree,',
         'InstallationData { cx: current, tree: product.tree,',
         'check/body_execution :: renewal decisions preserve strict callbacks and single assembly output'),
        ('reuse-unnormalized-product', product_path,
         'captured: capture_transition(current, after, product.tree, true)',
         'captured: Some(product)',
         'check/body_product :: assembly certified capture matches strict transition and preserves refusal'),
        ('cold-after-installation', execution_path,
         'let (next, tree, captured) = body_product.installation_output(value)',
         'let _ = cold(fallback, start, d, sig)\n'
         '        let (next, tree, captured) = body_product.installation_output(value)',
         'check/body_execution :: renewal decisions preserve strict callbacks and single assembly output'),
        ('stale-inferred-publication', SUBJECT,
         'map.get(cx.fns, d.name), map.get(next.fns, d.name)',
         'map.get(cx.fns, d.name), map.get(cx.fns, d.name)',
         'check/scalar_replay :: assembly certified renewal matches strict products at actual scheduler entries'),
    ]
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
        for name, path, before, after, owner in certified_controls:
            for subject, source in originals.items():
                (root / subject).write_text(source)
            (root / path).write_text(edit(originals[path], before, after))
            status, output = run('test', root / SUBJECT)
            failure = re.search(r'^FAIL\s+' + re.escape(owner) + r'\n\s+'
                                r'(?:assertion failed:|renewal decision repeated successful assembly)',
                                output, re.M)
            if not status or not failure or re.search(
                    r'^error:|VerifyError|Exception Details:|ClassFormatError', output, re.M):
                raise RuntimeError(name + ' missed its compiling certified-capture assertion\n' + output)
            print('OK: body renewal ' + name + ' -> ' + owner, flush=True)
    print(f'OK: {len(controls) + len(certified_controls)} compiling renewal controls, {time.monotonic() - started:.2f}s')


if __name__ == '__main__':
    main()
