#!/usr/bin/env python3
"""Require actual body work, private owners, and teardown in opt-in LSP Sessions.

Each isolated production mutation runs the complete server test closure. Only
its exact owning assertion is accepted: compiler, linker, timeout, incidental
panic, and unrelated test failures are not negative-control evidence. These
controls do not claim to distinguish prepared loading from safe legacy reparsing.
"""
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "incremental-semantics-contract"))
from cold import ROOT, edit, run


SUBJECT = "selfhost/src/lsp/server.dawn"
STANDALONE = "prepared standalone LSP owners preserve queries counts and shared lease lifetime"
PROJECT = "prepared project LSP owners drop conflicts and recreate leases on reopen"


def variants(original):
    project_call = "incremental.analyze_prepared(ws0.cache, prepared)"
    standalone_call = "incremental.analyze_prepared(owner, prepared)"
    prefix_project = ("incremental.new(st.std, ct_default(), ws0.lease.jsig, st.host.probe, "
                      "st.analysis_config.max_modules, st.analysis_config.max_text_units)")
    prefix_standalone = ("incremental.new(st.std, ct_default(), lease.jsig, st.host.probe, "
                         "st.analysis_config.max_modules, st.analysis_config.max_text_units)")
    previous_owner = "let owner = match previous { Some(owner) -> owner, None -> analysis_session(st, lease.jsig) }"
    controls = [
        ("project-body-admission-disabled", project_call,
         f"incremental.analyze_prepared({prefix_project}, prepared)", PROJECT,
         "stats.checked_bodies == 1 && stats.reused_bodies == 1"),
        ("standalone-body-admission-disabled", standalone_call,
         f"incremental.analyze_prepared({prefix_standalone}, prepared)", STANDALONE,
         'map.get(warm.docs, uri).expect("first").analysis_stats.expect("counts").checked_bodies == 2'),
        ("standalone-owner-lost", previous_owner,
         "let owner = analysis_session(st, lease.jsig)", STANDALONE,
         "stats.reused_modules == 1 && stats.visited_bodies == 0"),
        ("project-owner-lost", project_call,
         "incremental.analyze_prepared(analysis_session(st, ws0.lease.jsig), prepared)", PROJECT,
         "stats.checked_bodies == 1 && stats.reused_bodies == 1"),
        ("standalone-owner-shared", previous_owner,
         """let owner = if map.len(st.docs) > 0 {
      map.get(st.docs, sort(map.keys(st.docs))[0]).expect("shared document").standalone_cache.expect("shared owner")
    } else { match previous { Some(owner) -> owner, None -> analysis_session(st, lease.jsig) } }""",
         STANDALONE,
         'map.get(warm.docs, second_uri).expect("second").analysis_stats.expect("counts").checked_bodies == 2'),
        ("standalone-teardown-retains-docs",
         "docs: no_docs, workspaces: no_workspaces, standalone: none",
         "docs: if map.len(st0.workspaces) == 0 { st0.docs } else { no_docs }, workspaces: no_workspaces, standalone: none",
         STANDALONE,
         "count(closed) == 1 && map.len(stopped.docs) == 0 && map.len(stopped.workspaces) == 0"),
        ("cold-reuse-enabled",
         "Cold -> incremental.disable_reuse(incremental.new(st.std, ct_default(), js, st.host.probe,\n      config.max_modules, config.max_text_units))",
         "Cold -> incremental.new(st.std, ct_default(), js, st.host.probe,\n      config.max_modules, config.max_text_units)",
         PROJECT, "cold_stats.reused_modules == 0 && cold_stats.retained_total_modules == 0"),
    ]
    return [(name, edit(original, old, new), owner, assertion)
            for name, old, new, owner, assertion in controls]


def verify(name, status, output, owner, assertion):
    failures = re.findall(r"^FAIL\s+([^\n]+)", output, re.M)
    if owner is None:
        if status or failures or not re.search(r"^651 test\(s\) passed$", output, re.M):
            raise RuntimeError("Positive prepared LSP closure did not pass all 651 tests\n" + output)
        return
    label = "lsp/server :: " + owner
    exact = r"^FAIL\s+" + re.escape(label) + r"\n\s+assertion failed: " + re.escape(assertion) + r"\s*$"
    if (not status or failures != [label] or not re.search(exact, output, re.M)
            or re.search(r"^error:|Exception in thread|LinkageError", output, re.M)):
        raise RuntimeError(name + " missed its sole exact owning assertion\n" + output)


def self_test():
    owner, assertion = STANDALONE, "stats.checked_bodies == 1 && stats.reused_bodies == 1"
    failure = f"FAIL  lsp/server :: {owner}\n  assertion failed: {assertion}\n"
    verify("self-test", 1, failure, owner, assertion)
    rejected = [
        (0, failure), (1, "error: does not compile\n"),
        (1, failure.replace(assertion, "false")),
        (1, failure.replace(owner, PROJECT)),
        (1, failure + "FAIL  lsp/server :: unrelated test\n  assertion failed: false\n"),
        (1, failure + "Exception in thread main\n"),
        (1, failure + "LinkageError\n"),
        (1, failure.replace("assertion failed: " + assertion, "missing owner")),
    ]
    for status, output in rejected:
        try:
            verify("self-test", status, output, owner, assertion)
        except RuntimeError:
            continue
        raise RuntimeError("Prepared LSP failure oracle accepted unrelated evidence")
    print(f"OK: prepared LSP assertion oracle rejects {len(rejected)} false controls")


def main():
    started = time.monotonic()
    original = (ROOT / SUBJECT).read_text()
    controls = variants(original)
    if sys.argv[1:] == ["--self-test"]:
        self_test()
        return
    if sys.argv[1:] == ["--check-anchors"]:
        print(f"OK: {len(controls)} prepared LSP production anchors; no compiler invoked")
        return
    if sys.argv[1:]:
        raise SystemExit("usage: prepared-lifecycle.py [--check-anchors | --self-test]")
    with tempfile.TemporaryDirectory(prefix="dawn-prepared-lsp-lifecycle-") as temp:
        root = Path(temp)
        for directory in ("selfhost", "compiler-plan"):
            shutil.copytree(ROOT / directory, root / directory,
                            ignore=shutil.ignore_patterns("build", ".dawn"))
        (root / "packages").symlink_to(ROOT / "packages", target_is_directory=True)
        target = root / SUBJECT
        for name, source, owner, assertion in [("positive", original, None, None)] + controls:
            target.write_text(source)
            before = time.monotonic()
            status, output = run("test", target)
            verify(name, status, output, owner, assertion)
            print(f"OK: prepared LSP {name}, {time.monotonic() - before:.2f}s", flush=True)
    print(f"OK: {len(controls)} compiling prepared LSP controls, {time.monotonic() - started:.2f}s")


if __name__ == "__main__":
    main()
