#!/usr/bin/env python3
"""Require declaration identity admission to survive compiling negative controls.

The private subject keeps its parser and declaration index together; only
production identity admission changes, never the owning test assertions.
"""
import re
import shutil
import tempfile
import time
from pathlib import Path

from cold import ROOT, edit, run


def main():
    started = time.monotonic()
    original = (ROOT / "selfhost/src/check/identity.dawn").read_text()
    variants = [
        ("duplicate-parent", "if unique { out = out ++ [e] }", "out = out ++ [e]"),
        # The derived id: what it is a function of, and what it must not be a
        # function of. The spelling is the whole input, so dropping the kind
        # letter or the owner from it merges two declarations, and skipping
        # the finalizer leaves the low bits -- the only ones a 47-bit band
        # keeps, and the only ones `Map[Int, _]` buckets on -- correlated
        # across two names that differ in one character.
        ("spelling-drops-kind", 'Named(kind, name) -> "N" ++ kind_text(kind) ++ atom(name)',
         'Named(kind, name) -> "N" ++ atom(name)'),
        ("spelling-drops-owner", "pub fn minted_text(m: Minted) -> String = atom(m.owner) ++ path_text",
         "pub fn minted_text(m: Minted) -> String = atom(\"\") ++ path_text"),
        ("derive-skips-finalizer", "derived_floor() + (fmix64(fnv1a64(spelling)) & derived_width())",
         "derived_floor() + (fnv1a64(spelling) & derived_width())"),
        ("derive-leaves-the-band", "pub fn derived_floor() -> Int = 4294967296",
         "pub fn derived_floor() -> Int = 7"),
        ("derive-overflows-the-label-key", "pub fn derived_width() -> Int = 140737488355327",
         "pub fn derived_width() -> Int = 9223372036854775807"),
        ("parent-not-checked", "var depth = 1", "var depth = len(e.entry.key.path)"),
        ("binder-spelling", "return BoundType(i)", "return NamedType(name, [], [])"),
        ("default-ambiguity", "if same == 1 {", "if same >= 1 {"),
        ("world-erased", "DeclKey { scope: scope, path: path }",
         'DeclKey { scope: ModuleKey { ..scope, world: "shared" }, path: path }'),
        ("effect-binder-spelling", "return ProjectedEffect(i, parts[1])", "return NamedEffect(name)"),
        ("effect-member-erased", "return ProjectedEffect(i, parts[1])", 'return ProjectedEffect(i, "")'),
        ("effect-binder-slot", "return ProjectedEffect(i, parts[1])", "return ProjectedEffect(0, parts[1])"),
    ]
    with tempfile.TemporaryDirectory(prefix="dawn-declaration-identity-") as temp:
        root = Path(temp)
        for directory in ("selfhost", "compiler-plan"):
            shutil.copytree(ROOT / directory, root / directory,
                            ignore=shutil.ignore_patterns("build", ".dawn"))
        (root / "packages").symlink_to(ROOT / "packages", target_is_directory=True)
        target = root / "selfhost/src/check/identity.dawn"
        for name, source in [("positive", original)] + [(n, edit(original, a, b)) for n, a, b in variants]:
            target.write_text(source)
            status, output = run("test", target)
            if name == "positive":
                if status:
                    raise RuntimeError("Positive identity subject failed\n" + output)
            elif not status or not re.search(r"^FAIL\s+check/identity :: identity [^\n]*\n\s+assertion failed:", output, re.M):
                raise RuntimeError(name + " did not reach its owning assertion\n" + output)
            print("OK: declaration identity " + name, flush=True)
    print(f"OK: declaration identity and {len(variants)} compiling mutants, {time.monotonic() - started:.2f}s")


if __name__ == "__main__":
    main()
