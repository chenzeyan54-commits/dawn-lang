#!/usr/bin/env python3
"""Keep single-spelling publication equivalent to the full callee scan.

Private compiler copies isolate each fault. A compiling control must reach a
publication assertion, not fail to parse, link, or execute its test program.
This prerequisite does not enable inferred-body replay by itself.
"""
import re
import shutil
import tempfile
import time
from pathlib import Path

from cold import ROOT, edit, run


SUBJECT = "selfhost/src/check/callee_index.dawn"


def main():
    started = time.monotonic()
    original = (ROOT / SUBJECT).read_text()
    guard = "if map.get(original.local_functions, name) != before { return None }"
    noop = "if before == after { return Some(ix) }"
    variants = [
        ("previous-local-state", guard, "if false { return None }"),
        ("unchecked-noop", guard + "\n  " + noop, noop + "\n  " + guard),
        ("old-observation-retained", "table = remove_observation(table, sig)?", "table = table"),
        ("duplicate-count", "map.insert(bucket, sig, count + 1)", "map.insert(bucket, sig, 1)"),
        ("remove-one-not-all", "if count == 1 { map.remove(bucket, sig) }", "if true { map.remove(bucket, sig) }"),
        ("decrement-contributor", "map.insert(bucket, sig, count - 1)", "map.insert(bucket, sig, count)"),
        ("deleted-canonical-key", "by_key: map.remove(table.by_key, slot)", "by_key: table.by_key"),
        ("local-publication-state", "local_functions: map.insert(table.local_functions, name, sig)", "local_functions: table.local_functions"),
        ("local-deletion-state", "local_functions: map.remove(table.local_functions, name)", "local_functions: table.local_functions"),
        ("filtered-old-observation", "if sig.is_builtin || sig.trait_id != None { return Some(table) }", "if false { return Some(table) }"),
        ("new-observation-missing", "observations: observe(table.observations, sig)", "observations: table.observations"),
    ]
    with tempfile.TemporaryDirectory(prefix="dawn-callee-publication-") as temp:
        root = Path(temp)
        for directory in ("selfhost", "compiler-plan"):
            shutil.copytree(ROOT / directory, root / directory,
                            ignore=shutil.ignore_patterns("build", ".dawn"))
        (root / "packages").symlink_to(ROOT / "packages", target_is_directory=True)
        target = root / SUBJECT
        for name, source in [("positive", original)] + [
                (name, edit(original, old, new)) for name, old, new in variants]:
            target.write_text(source)
            status, output = run("test", target)
            if name == "positive":
                if status or "test(s) passed" not in output:
                    raise RuntimeError("Positive publication failed\n" + output)
            elif not status or not re.search(
                    r"^FAIL\s+check/callee_index :: callee publication [^\n]*\n\s+assertion failed:",
                    output, re.M) or re.search(r"^error:", output, re.M):
                raise RuntimeError(name + " missed its assertion owner\n" + output)
            print("OK: callee publication " + name, flush=True)
    print(f"OK: {len(variants)} compiling callee publication controls, {time.monotonic() - started:.2f}s")


if __name__ == "__main__":
    main()
