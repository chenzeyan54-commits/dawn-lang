#!/usr/bin/env python3
"""Require raw source equality guards to fail their own compiling controls.

Private copies preserve the shared worktree. A compilation error, runtime
panic, unrelated assertion or missing test result cannot establish refusal.
"""
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

from cold import ROOT, edit, run


CASES = (
    ("accept-negative-range", "  if old_lo < 0 || old_hi < old_lo || new_lo < 0 || new_hi < new_lo { return false }",
     "  if old_hi < old_lo || new_hi < new_lo { return false }",
     "source text equality matches the raw oracle over code point ranges"),
    ("reject-valid-empty", "  if old_hi - old_lo != new_hi - new_lo { return false }",
     "  if old_lo == old_hi { return false }\n  if old_hi - old_lo != new_hi - new_lo { return false }",
     "source text equality matches the raw oracle over code point ranges"),
    ("ignore-cached-trivia", "    (Some(left), Some(right)) -> return left == right",
     "    (Some(left), Some(right)) -> return true",
     "source text equality rejects equal width trivia mutations"),
    ("cache-unreachable-boundary", "    if raw_offset == hi {", "    if true {",
     "source raw range cache never trusts supplied code points or token spelling"),
)


def rejected(status, output, owner):
    failures = re.findall(r"^FAIL\s+check/source_projection :: (.+)\n\s+assertion failed:", output, re.M)
    return (status == 1 and owner in failures and
            not re.search(r"^error:|Exception|Error|panic:|Panic|timed out", output, re.M) and
            re.search(r"\d+ test\(s\) failed", output) is not None)


def selftest():
    owner = CASES[0][3]
    sample = f"FAIL  check/source_projection :: {owner}\n  assertion failed: oracle\n1 test(s) failed\n"
    assert rejected(1, sample, owner)
    for status, output in ((0, sample), (2, sample), (1, "error: cannot compile\n" + sample),
                           (1, sample + "Panic\n"), (1, sample + "java.lang.VerifyError\n"),
                           (1, sample.replace(owner, "unrelated")), (1, sample.split("1 test")[0]),
                           (1, "timed out\n" + sample)):
        assert not rejected(status, output, owner)
    print("OK: source equality classifier rejects eight invalid outcomes")


def main():
    original = (ROOT / "selfhost/src/check/source_projection.dawn").read_text()
    subjects = [("positive", original, None)] + [
        (name, edit(original, old, new), owner) for name, old, new, owner in CASES]
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="dawn-source-equality-") as temporary:
        root = Path(temporary)
        for directory in ("selfhost", "compiler-plan"):
            shutil.copytree(ROOT / directory, root / directory,
                            ignore=shutil.ignore_patterns("build", ".dawn"))
        (root / "packages").symlink_to(ROOT / "packages", target_is_directory=True)
        target = root / "selfhost/src/check/source_projection.dawn"
        for name, source, owner in subjects:
            target.write_text(source)
            before = time.monotonic()
            status, output = run("test", target)
            if owner is None:
                if status or "test(s) passed" not in output or re.search(r"^FAIL|^error:", output, re.M):
                    raise RuntimeError("Source equality positive failed\n" + output)
            elif not rejected(status, output, owner):
                raise RuntimeError(name + " missed its named assertion owner\n" + output)
            print(output.strip(), flush=True)
            print(f"OK: source equality {name}; elapsed={time.monotonic() - before:.2f}s", flush=True)
    print(f"OK: source equality positive and four compiling controls; elapsed={time.monotonic() - started:.2f}s")


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        selftest()
    elif len(sys.argv) == 1:
        main()
    else:
        raise SystemExit("usage: source-equality.py [--self-test]")
