#!/usr/bin/env python3
"""Guard opaque loader proofs at the source binding and consumption boundaries.

A missing proof for clean, eligible source must stay missing, even with an old
cache. Equal-AST loader collisions must not borrow another source's proof.
Each isolated mutation must compile and fail its exact owning assertion; other
test failures, compiler errors, and linker failures cannot substitute for it.
"""
import re
import shutil
import tempfile
import time
from pathlib import Path

from cold import ROOT, edit, run


SUBJECT = "selfhost/src/driver/analyze.dawn"


def main():
    started = time.monotonic()
    original = (ROOT / SUBJECT).read_text()
    variants = [
        ("reparse-only-missing-prepared-proof",
         "Some(snapshot) -> snapshot\n            None -> match source_snapshot.of(mf.text)",
         """Some(snapshot) -> match snapshot {
              Some(proof) -> Some(proof)
              None -> match source_snapshot.of(mf.text) {
                Some(parsed) -> source_snapshot.resolved(parsed, mf.m)
                None -> None
              }
            }
            None -> match source_snapshot.of(mf.text)""",
         "prepared module execution refuses a clean missing proof without reparsing",
         "refused.counts == None && refused.cache == None"),
        ("collision-source-identity",
         "if proof.path == raw.path && proof.text == raw.text {",
         "if true {",
         "prepared loader collisions never certify a replacement file's text",
         "source_snapshot.indexed(snapshot) == source_snapshot.indexed(expected)"),
    ]
    subjects = [("positive", original, None, None)] + [
        (name, edit(original, old, new), owner, assertion)
        for name, old, new, owner, assertion in variants]
    with tempfile.TemporaryDirectory(prefix="dawn-prepared-proofs-") as temp:
        root = Path(temp)
        for directory in ("selfhost", "compiler-plan"):
            shutil.copytree(ROOT / directory, root / directory,
                            ignore=shutil.ignore_patterns("build", ".dawn"))
        (root / "packages").symlink_to(ROOT / "packages", target_is_directory=True)
        target = root / SUBJECT
        for name, source, owner, assertion in subjects:
            target.write_text(source)
            before = time.monotonic()
            status, output = run("test", target)
            if name == "positive":
                if status or "test(s) passed" not in output:
                    raise RuntimeError("Positive prepared proof tests failed\n" + output)
            else:
                owning_failure = (r"^FAIL\s+driver/analyze :: " + re.escape(owner)
                                  + r"\n\s+assertion failed: " + re.escape(assertion) + r"\s*$")
                if not status or not re.search(owning_failure, output, re.M) or re.search(
                        r"^error:|Exception in thread|LinkageError", output, re.M):
                    raise RuntimeError(name + " missed its exact assertion owner\n" + output)
            print(f"OK: prepared proofs {name}, {time.monotonic() - before:.2f}s", flush=True)
    print(f"OK: {len(variants)} compiling prepared proof controls, {time.monotonic() - started:.2f}s")


if __name__ == "__main__":
    main()
