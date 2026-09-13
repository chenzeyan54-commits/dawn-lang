#!/usr/bin/env python3
"""Guard production provenance producers, not only table transformations.

Private source copies must reach the exact transition owning assertion. A
build failure, linker failure or unrelated test cannot stand in for the guard.
"""
import re
import shutil
import tempfile
import time
from pathlib import Path

from cold import ROOT, edit, run


def main():
    started = time.monotonic()
    driver_path = "selfhost/src/driver/analyze.dawn"
    std_path = "selfhost/src/driver/stdlib.dawn"
    allocation_path = "selfhost/src/check/allocation.dawn"
    identity_path = "selfhost/src/check/identity.dawn"
    paths = (driver_path, std_path, allocation_path, identity_path)
    originals = {path: (ROOT / path).read_text() for path in paths}
    variants = [
        ("drop-local-origin", driver_path, "local_provenance = allocation.table(entries ++ methods)", "local_provenance = None"),
        ("lose-provider-carry", driver_path, "map.insert(origin.modules, mf.mod_path, local_provenance)", "map.from([(mf.mod_path, local_provenance)])"),
        ("admit-header-errors", driver_path, "if len(headers.cx.diags) == 0 {", "if true {"),
        ("drop-std-origin", std_path, "header_origin = allocation.table(entries ++ methods)", "header_origin = None"),
        ("skip-std-world", driver_path, 'allocation.in_world(table, "std", world)', "Some(table)"),
        ("drop-compiler-origin", driver_path, "intrinsics: allocation.compiler_headers(world)", "intrinsics: None"),
        # The intern table behind the derived nominal and trait ids travels on
        # the same carry as `next_id`, and for the same reason: a digest
        # collision between two modules is as fatal as one inside a module and
        # only a program-wide table can see it.
        ("drop-identity-carry", driver_path, "identities: cx.identities,", "identities: before.identities,"),
        ("drop-std-identity-carry", std_path, "identities: interned,\n    mods: mods,",
         "identities: map.empty(),\n    mods: mods,"),
        ("drop-std-identity-step", std_path, "interned = cx1.identities", "interned = interned"),
        # The consumer side of the same carry. Each of these four is a way a
        # consumer could acquire a binder it was never handed: drop the
        # provider ledger and resolve the reference anyway, mint the provider's
        # own binding, let a consumer declaration take an allocation the
        # provider already owns, or match a renamed declaration by position.
        ("drop-provider-reference", driver_path, "Some(Some(provider)) -> { tables = tables ++ [provider] }", "Some(Some(_)) -> ()"),
        ("mint-provider-identity", allocation_path, "if id != e.id", "if false"),
        ("collide-same-domain", allocation_path, "if binding != e.binding", "if false"),
        ("rename-blind-identity", identity_path, "let path = [Named(FunctionDecl, f.name)]", 'let path = [Named(FunctionDecl, "${i}")]'),
    ]
    with tempfile.TemporaryDirectory(prefix="dawn-provenance-carry-") as temp:
        root = Path(temp)
        for directory in ("selfhost", "compiler-plan"):
            shutil.copytree(ROOT / directory, root / directory, ignore=shutil.ignore_patterns("build", ".dawn"))
        (root / "packages").symlink_to(ROOT / "packages", target_is_directory=True)
        subjects = [("positive", driver_path, originals[driver_path])] + [
            (name, path, edit(originals[path], old, new)) for name, path, old, new in variants]
        for name, path, source in subjects:
            (root / path).write_text(source)
            status, output = run("test", root / driver_path)
            failure = re.search(r"^FAIL\s+driver/analyze :: module provenance carry keeps exporting owners across header reorder[^\n]*\n\s+assertion failed:", output, re.M)
            if (status if name == "positive" else not status or not failure):
                raise RuntimeError(name + " did not satisfy its owning contract\n" + output)
            (root / path).write_text(originals[path])
            print("OK: provenance carry " + name, flush=True)
    print(f"OK: provenance producers and {len(variants)} compiling mutants, {time.monotonic() - started:.2f}s")


if __name__ == "__main__":
    main()
