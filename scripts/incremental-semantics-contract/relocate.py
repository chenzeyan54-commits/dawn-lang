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
        # What is left of this module is one question it answers from the
        # value in hand, and the controls follow it. A binder does not move
        # between revisions any more -- it is `identity.pack` of its
        # declaration and its slot -- so the tables, the intervals and every
        # control that turned one of them off went with the production code
        # they mutated (K5). The ABI permutation followed in K6: a row is
        # ordered by effect id and an id derives from its declaration, so the
        # permutation could only be the identity, and the refusal it carried
        # is a width comparison in `check/relocate_tree` now.
        #
        # The question: which band is this evidence key in, and does the name
        # beside it spell the same thing. Each band is read back by arithmetic
        # on the key, and each reading owns a control.
        ("label-band-effect", 'let prefix = ev_label_name(key_effect_id(key), "")',
         'let prefix = ev_label_name(key, "")'),
        ("role-band-label", "if key_is_label(key) { return Some(LabelSlot(key_effect_id(key))) }",
         "if key_is_label(key) { return Some(LabelSlot(key)) }"),
        ("associated-band-trait", "  let tr = key_trait_id(key)\n  let subject = assoc_subject(s.name, tr)?",
         "  let tr = key\n  let subject = assoc_subject(s.name, tr)?"),
        ("unknown-evidence-spelling", "if name != ev_var_name(key_var_id(key)) { return None }",
         "if false { return None }"),
        ("unminted-associated-spelling",
         'if member == "" || ev_assoc_name(tv, tr, member) != name { return None }',
         "if false { return None }"),
        ("unchecked-symbol-spelling",
         "    Some(key) -> {\n      let _ = evidence_name(s.name, key)?\n      Some(s)\n    }",
         "    Some(key) -> Some(s)"),
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
