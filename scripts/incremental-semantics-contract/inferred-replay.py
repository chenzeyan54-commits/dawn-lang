#!/usr/bin/env python3
"""Require inferred reuse to preserve scheduler publication and entry evidence.

Each fault compiles a private compiler copy and must fail an inferred replay
assertion. Parser, compiler, linker, and incidental runtime failures are not
evidence that the publication contract is protected.
"""
import re
import shutil
import tempfile
import time
from pathlib import Path

from cold import ROOT, edit, run


REPLAY = "selfhost/src/check/scalar_replay.dawn"
PRODUCT = "selfhost/src/check/body_product.dawn"


def main():
    started = time.monotonic()
    originals = {path: (ROOT / path).read_text() for path in (REPLAY, PRODUCT)}
    refresh = """    Some(p) -> match callee_index.replace_local(p.callees, d.name,
      map.get(cx.fns, d.name), map.get(next.fns, d.name)) {
      Some(ix) -> Some(Prepared { ..p, callees: ix })
      None -> None
    }"""
    variants = [
        ("own-entry-publication", REPLAY,
         "if sig.inferring && map.get(cx.fns, d.name) != Some(sig) { return (prepared, Rejected) }",
         "if false { return (prepared, Rejected) }"),
        ("entry-not-sealed-pairing", REPLAY,
         "allocation.body_relocation(prior_sig, sig, no_evidence)",
         "allocation.body_relocation(p.tree.sig, sig, no_evidence)"),
        ("sealed-signature-metadata", REPLAY,
         "if not primitive(p.tree.sig.ret) || p.tree.sig != sealed ||",
         "if not primitive(p.tree.sig.ret) ||"),
        ("empty-present-read-log", REPLAY,
         "if reads == [] && not inferred { return None }",
         "if reads == [] { return None }"),
        ("absent-read-log", REPLAY,
         "let reads = p.function_reads?",
         "let reads = p.function_reads.unwrap_or([])"),
        ("own-signature-write-required", REPLAY,
         "set.len(bounds_written) != set.len(bound_ids) || published != inferred",
         "set.len(bounds_written) != set.len(bound_ids)"),
        ("completed-recording-index", REPLAY,
         "let callees = callee_index.of(completed)",
         "let callees = callee_index.of(old.cx)"),
        ("skip-hit-publication", REPLAY, refresh,
         refresh.replace("Some(p) -> match", "Some(p) -> if outcome.counts.reused != stepped.counts.reused { Some(p) } else { match") + " }"),
        ("skip-cold-publication", REPLAY, refresh,
         refresh.replace("Some(p) -> match", "Some(p) -> if outcome.counts.reused == stepped.counts.reused { Some(p) } else { match") + " }"),
        ("failed-refresh-keeps-index", REPLAY, refresh,
         refresh.replace("None -> None", "None -> Some(p)")),
        ("inferred-hit-counter", REPLAY,
         "Some(after) -> (reused(stepped), after, product.tree)\n      None -> cold.inferred_body",
         "Some(after) -> (stepped, after, product.tree)\n      None -> cold.inferred_body"),
        ("install-sealed-signature", PRODUCT,
         "fns: apply_changes(current.fns, product.signatures),",
         "fns: current.fns,"),
    ]
    subjects = [("positive", REPLAY, originals[REPLAY])] + [
        (name, path, edit(originals[path], old, new))
        for name, path, old, new in variants]
    with tempfile.TemporaryDirectory(prefix="dawn-inferred-replay-") as temp:
        root = Path(temp)
        for directory in ("selfhost", "compiler-plan"):
            shutil.copytree(ROOT / directory, root / directory,
                            ignore=shutil.ignore_patterns("build", ".dawn"))
        (root / "packages").symlink_to(ROOT / "packages", target_is_directory=True)
        failures = []
        for name, path, source in subjects:
            for original_path, text in originals.items():
                (root / original_path).write_text(text)
            (root / path).write_text(source)
            status, output = run("test", root / REPLAY)
            if name == "positive":
                if status or "test(s) passed" not in output:
                    raise RuntimeError("Positive inferred replay failed\n" + output)
            elif not status or not re.search(
                    r"^FAIL\s+check/scalar_replay :: scalar replay inferred [^\n]*\n\s+assertion failed:",
                    output, re.M) or re.search(r"^error:|Exception in thread|LinkageError", output, re.M):
                failures.append(name)
                print(name + " missed its inferred assertion owner\n" + output, flush=True)
                continue
            print("OK: inferred replay " + name, flush=True)
        if failures:
            raise RuntimeError("Inferred replay controls failed: " + ", ".join(failures))
    print(f"OK: {len(variants)} compiling inferred replay controls, {time.monotonic() - started:.2f}s")


if __name__ == "__main__":
    main()
