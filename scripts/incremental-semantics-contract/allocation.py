#!/usr/bin/env python3
"""Reject broken provenance joins through compiling owning negative controls.

Use a private compiler source copy so the join changes without changing the
test assertions or any concurrently used worktree.
"""
import re
import shutil
import tempfile
import time
from pathlib import Path

from cold import ROOT, edit, run


def main():
    started = time.monotonic()
    original = (ROOT / "selfhost/src/check/allocation.dawn").read_text()
    variants = [
        ("constant-type", "if relocate.ty(header_ids, old_ty) != Some(next_ty) { return None }", ""),
        ("binding-conflict", "if id != e.id", "if false"),
        ("owner-conflict", "if binding != e.binding", "if false"),
        ("target-identity", "map.get(b.bindings, binding)", "map.get(a.bindings, binding)"),
        ("effect-domain", "EffectParameter -> { effects = map.insert(effects, id, target) }",
         "EffectParameter -> { types = map.insert(types, id, target) }"),
        ("compiler-trait-binders", "entries = entries ++ binders(key, [tr.tvar], [])?", "entries = entries"),
        ("negative-slot", "HeaderSlot(index) -> if index < 0", "HeaderSlot(index) -> if false"),
        # A body's interval is arithmetic; the evidence pack is the one thing
        # inside it that is not, so the permutation and the extent of the
        # interval each own a control.
        ("interval-evidence",
         "moved_evidence = map.insert(moved_evidence, id, target + evidence[0] - start + index)",
         "moved_evidence = map.insert(moved_evidence, id, target + id - start)"),
        ("interval-ghost", "relocate.body_interval(header_ids, start, count, target, moved_evidence)",
         "relocate.body_interval(header_ids, start, count - 1, target, moved_evidence)"),
        ("reserved-signature", "if moved != next_sig { return None }", "if false { return None }"),
        ("module-combine", "  table(entries)\n}\n\n## Rebind", "  table([])\n}\n\n## Rebind"),
        ("world-owner", "if declaration.scope.world != from", "if false"),
        ("source-wildcard", 'None -> scope.source == ""', "None -> true"),
        ("compiler-erasure", 'entries = entries ++ [entry(compiler_key(world, TypeDecl, "erased_runtime"), TypeParameter, 0, id)]',
         "entries = entries"),
    ]
    # The nominal and trait halves of this ledger are gone: those two domains
    # derive their integers from the declaration, so a consumer needs no
    # mapping and two declarations cannot quarrel over one number. What
    # inherited the ledger's job on those domains is `cx.mint` -- the intern
    # table and its collision refusal -- so those verdicts are held here too.
    cx_original = (ROOT / "selfhost/src/check/cx.dawn").read_text()
    cx_variants = [
        ("intern-collision-ignored", "Some(other) -> if other != decl {", "Some(other) -> if false {"),
        ("intern-not-recorded", "(Cx { ..cx, identities: map.insert(cx.identities, id, decl) }, id)", "(cx, id)"),
        ("mint-takes-the-counter", "(Cx { ..cx, identities: map.insert(cx.identities, id, decl) }, id)",
         "(Cx { ..cx, next_id: cx.next_id + 1, identities: map.insert(cx.identities, id, decl) }, id)"),
        ("mint-reads-the-source-path", 'let decl = minted(cx.owner_class.unwrap_or(""), kind, name)',
         'let decl = minted(cx.owner_class.unwrap_or("") ++ cx.src_path.unwrap_or(""), kind, name)'),
        ("mint-ignores-the-kind", 'let decl = minted(cx.owner_class.unwrap_or(""), kind, name)',
         'let decl = minted(cx.owner_class.unwrap_or(""), identity.TypeDecl, name)'),
    ]
    with tempfile.TemporaryDirectory(prefix="dawn-allocation-") as temp:
        root = Path(temp)
        for directory in ("selfhost", "compiler-plan"):
            shutil.copytree(ROOT / directory, root / directory,
                            ignore=shutil.ignore_patterns("build", ".dawn"))
        (root / "packages").symlink_to(ROOT / "packages", target_is_directory=True)
        target = root / "selfhost/src/check/allocation.dawn"
        for name, source in [("positive", original)] + [(n, edit(original, a, b)) for n, a, b in variants]:
            target.write_text(source)
            status, output = run("test", target)
            if name == "positive":
                if status:
                    raise RuntimeError("Positive allocation subject failed\n" + output)
            elif not status or not re.search(r"^FAIL\s+check/allocation :: allocation [^\n]*\n\s+assertion failed:", output, re.M):
                raise RuntimeError(name + " did not reach its owning assertion\n" + output)
            print("OK: allocation " + name, flush=True)
        target.write_text(original)
        cx_target = root / "selfhost/src/check/cx.dawn"
        for name, source in [("positive", cx_original)] + [(n, edit(cx_original, a, b)) for n, a, b in cx_variants]:
            cx_target.write_text(source)
            status, output = run("test", cx_target)
            if name == "positive":
                if status:
                    raise RuntimeError("Positive mint subject failed\n" + output)
            elif not status or not re.search(r"^FAIL\s+check/cx :: (a minted id|two declarations) [^\n]*\n\s+assertion failed:", output, re.M):
                raise RuntimeError(name + " did not reach its owning assertion\n" + output)
            print("OK: derived identity " + name, flush=True)
    print(f"OK: allocation and {len(variants) + len(cx_variants)} compiling mutants, "
          f"{time.monotonic() - started:.2f}s")


if __name__ == "__main__":
    main()
