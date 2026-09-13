#!/usr/bin/env python3
"""Prove semantic-domain relocation with compiling mutations of its real code.

Only the mapper changes in each private subject. Every negative must reach a
named assertion; a seed, syntax, linking or runtime capability failure is not
evidence that missing references and namespace mixups are caught.
"""
import re
import shutil
import tempfile
import time
from pathlib import Path

from cold import ROOT, edit, run


def owning_assertion(output):
    return bool(re.search(r"^FAIL\s+check/relocate :: relocation [^\n]*\n\s+assertion failed:", output, re.M))


def main():
    started = time.monotonic()
    assert owning_assertion("FAIL  check/relocate :: relocation control\n  assertion failed: expected\n")
    assert not owning_assertion("FAIL  check/relocate :: relocation control\n  NoSuchMethodError\n")
    assert not owning_assertion("FAIL  elsewhere :: relocation control\n  assertion failed: expected\n")
    original = (ROOT / "selfhost/src/check/relocate.dawn").read_text()
    variants = [
        ("implicit-identity", "match map.get(ids.type_vars, id) { Some(moved) -> Some(moved), None -> shifted(ids, id) }",
         "Some(id)"),
        # A body's own allocations are an interval, not a table. These five
        # own the arithmetic that replaced the table: that it covers the whole
        # interval and not only the IDs with an explicit entry, that it
        # carries the evidence permutation, that it stops at the interval's
        # end, that two intervals cannot overlap, and that an override which
        # is not a permutation of the interval is refused.
        ("interval-entries-only",
         "Some(map.get(interval.evidence, id).unwrap_or(interval.target + id - interval.start))",
         "map.get(interval.evidence, id)"),
        ("interval-forgets-permutation",
         "Some(map.get(interval.evidence, id).unwrap_or(interval.target + id - interval.start))",
         "Some(interval.target + id - interval.start)"),
        ("interval-ghost",
         "if id >= interval.start && id < interval.start + interval.count {\n"
         "      return Some(map.get(interval.evidence, id).unwrap_or(interval.target + id - interval.start))",
         "if id >= interval.start && id < interval.start + interval.count - 1 {\n"
         "      return Some(map.get(interval.evidence, id).unwrap_or(interval.target + id - interval.start))"),
        ("interval-overlap",
         "if start < other.start + other.count && other.start < start + count { return None }",
         "if false { return None }"),
        ("interval-permutation",
         "for moved in set.to_list(moved_to) { if not set.has(moved_from, moved) { return None } }",
         "for moved in set.to_list(moved_to) { if false { return None } }"),
        ("interval-extent",
         "if interval.start == start && interval.count == count && interval.target == target { return true }",
         "if interval.start == start && interval.target == target { return true }"),
        ("noninjective-map", "not injective(ids)", "false"),
        ("wrong-evidence-key", "map.get(ids.evidence, key)", "map.get(ids.evidence, key + 1)"),
        # Nominal and trait references no longer move, so the three evidence
        # bands are read back from the key itself rather than from a table.
        # Each of the three readings owns a control: a variable key that the
        # relocation does not carry must still fail closed, and the effect and
        # trait a label or associated key names must be the one its arithmetic
        # says.
        ("identity-evidence-band", "None -> if key_is_variable(key) { None } else { Some(key) }",
         "None -> Some(key)"),
        ("label-band-effect", 'let prefix = ev_label_name(key_effect_id(key), "")',
         'let prefix = ev_label_name(key, "")'),
        ("associated-band-trait", "  let tr = key_trait_id(key)\n  for (tv, moved) in map.entries(ids.type_vars) {",
         "  let tr = key\n  for (tv, moved) in map.entries(ids.type_vars) {"),
        ("role-band-label", "if key_is_label(key) { return Some(LabelSlot(key_effect_id(key))) }",
         "if key_is_label(key) { return Some(LabelSlot(key)) }"),
        ("stale-label-order", "Some(eff_with_labels(moved, out))", "Some(ELabeled(moved, out))"),
        ("stale-union", "Some(eff_union(parts))", "Some(e)"),
        ("stale-opaque-target", "types(ids, args)?, ty(ids, target)?", "types(ids, args)?, target"),
        ("stale-function-effect", "effect_row(ids, eff)?", "eff"),
        ("stale-collection-child", "Some(TyArray(ty(ids, elem)?))", "Some(TyArray(elem))"),
        ("reversed-dictionary-domains", "Some((tr, type_var(ids, tv)?))",
         "Some((type_var(ids, tv)?, tr))"),
        ("stale-symbol-evidence", "Some(key) -> Some(evidence_key(ids, key)?)", "Some(key) -> Some(key)"),
        ("stale-handler-installation", "Some((local_id(ids, installation)?, ty(ids, value)?))",
         "Some((installation, ty(ids, value)?))"),
        ("stale-forwarded-witness", "Some(WForward(local_id(ids, id)?))", "Some(WForward(id))"),
        ("stale-evidence-spelling", "..s, name: name, ty:", "..s, name: s.name, ty:"),
        ("unknown-evidence-spelling", "if name != ev_var_name(old) { return None }",
         "if false { return None }"),
        ("stale-evidence-operand-order", "evidence_order(ids, s, signature(ids, s)?)",
         "evidence_order(ids, s, s)"),
        ("missing-parameter-evidence", "let abi = sig_abi_eff(s)", "let abi = s.eff"),
        ("lost-associated-subject", "out ++ [AssociatedSlot(tv, tr, name)]",
         "out ++ [AssociatedSlot(0, tr, name)]"),
        ("lost-associated-member", "out ++ [AssociatedSlot(tv, tr, name)]",
         'out ++ [AssociatedSlot(tv, tr, "")]'),
    ]
    subjects = [("positive", original)] + [(name, edit(original, old, new)) for name, old, new in variants]
    with tempfile.TemporaryDirectory(prefix="dawn-typed-relocation-") as temp:
        root = Path(temp)
        for directory in ("selfhost", "compiler-plan"):
            shutil.copytree(ROOT / directory, root / directory,
                            ignore=shutil.ignore_patterns("build", ".dawn"))
        (root / "packages").symlink_to(ROOT / "packages", target_is_directory=True)
        target = root / "selfhost/src/check/relocate.dawn"
        for name, source in subjects:
            target.write_text(source)
            status, output = run("test", target)
            if name == "positive":
                if status:
                    raise RuntimeError("Positive relocation subject failed\n" + output)
            elif not status or not owning_assertion(output):
                raise RuntimeError(name + " did not reach its owning assertion\n" + output)
            print("OK: typed relocation " + name, flush=True)
    print(f"OK: typed relocation and {len(variants)} compiling mutants, {time.monotonic() - started:.2f}s")


if __name__ == "__main__":
    main()
