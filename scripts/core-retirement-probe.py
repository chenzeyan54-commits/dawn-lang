#!/usr/bin/env python3
"""Evidence for K8: can `scripts/core-normalize.py` be retired?

`scripts/selfhost-core-diff.sh` keeps two goldens of the same dumps.
`selfhost.sha` is exact. `selfhost.norm.sha` is the same hashes with generated
`$Adt<N>` names consistently alpha-renamed, because type inference and ADT
declarations used to share a counter, so an unrelated addition anywhere
renumbered generated names everywhere (the reason is written out at
`scripts/selfhost-core-diff.sh:52-61`).

K4 gave nominal declarations derived ids, so that counter no longer moves them.
This probe measures whether the filter still filters. The criterion is copied
from `agent-handoff/identity-schemes-survey-20260914.md` section 3 (b):

  1. Take a set of representative edits: append an unrelated function, a
     `type`, an `effect`, and whitespace only, to an early module. Two more
     are added here, `type_used` and `effect_used`: an unreferenced `type` or
     `effect` is pruned before lowering, so on a tree with derived ids those
     two move nothing at all and pass vacuously.
  2. For each edit, compute the set of modules whose *exact* hash moved and the
     set whose *normalized* hash moved.
  3. Criterion A, the retirement criterion: the two sets are equal for every
     edit. Then normalization filters nothing and can be deleted.
  4. Criterion B, cleanliness: the set contains only the edited module (plus
     modules that genuinely depend on what it exports, which are printed for a
     human to judge). A failure here is NOT an argument for keeping the filter;
     survey step 4 says it is a signal that K4/K5 are not done, and the fix is
     to finish them. `Op<id>_` and bare effect-label integers
     (`ir/core.dawn:428-431, :445`) were never filterable anyway.
  5. Positive control, which cannot be skipped: run the same edits on a tree
     from before K4. Criterion A must fail there. Without it, green means
     "did not look", not "did not leak". Use `--expect-filtering` to make that
     run assert its own redness.

This is a measuring tool, not a gate. It is not wired into CI, and it does not
change `selfhost-core-diff.sh` or the goldens. K8 decides whether any of it
becomes a gate.

Measured 2026-09-14, on main de71450e (K4 in, K5 not yet): criterion A holds on
all 12 edits, criterion B fails on the 6 that reach Core. Every collateral diff
is a bare `int` inside an `ev$Pack`/`ctl` block, which is the effect-variable id
still coming from the shared `Cx.next_id`, i.e. the half K5 owns. The positive
control on 117a75d1 (pre-K4) failed criterion A on 10 of the same 12 edits, so
the probe does distinguish the two states. Full numbers in
`agent-handoff/k8-retirement-probe-20260914.md`.

Usage
-----

    scripts/core-retirement-probe.py                  # this tree
    scripts/core-retirement-probe.py --tree /path/to/old-worktree \
        --seed-cache ~/workspace/dawn-lang/.dawn/seeds --expect-filtering
    scripts/core-retirement-probe.py --self-test      # verdict negative control
    scripts/core-retirement-probe.py --only fn --targets std/list.dawn

The tree is never touched. Every edit is applied to a throwaway copy under
`--workdir` (a temporary directory by default), which is where `bin/dawn`
rebuilds its toolchain, so the first run on a tree without `build/` pays for
one bootstrap and the rest reuse it. Editing anything under `selfhost/src`
forces a jar rebuild; editing `std/` does not, because `bin/dawn` exports
DAWN_STD and the lowering reads std from disk.

Exit status is 0 when every criterion holds (or, under
`--expect-filtering`, when criterion A fails as the control requires).
"""

import argparse
import hashlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_TREE = HERE.parent

# Appended at the end of the file, never inserted, so no existing line moves:
# panic sites are baked with file:line and a shifted line is a real change that
# would drown the signal being measured.
EDITS = {
    "fn": """
## Probe (retirement-probe.py): an unrelated function.
pub fn probe_retirement_fn(x: Int) -> Int = x + 1
""",
    "type": """
## Probe (retirement-probe.py): an unrelated type.
pub type ProbeRetirementAdt =
  | ProbeRetirementAlpha
  | ProbeRetirementBeta(v: Int)
""",
    "effect": """
## Probe (retirement-probe.py): an unrelated effect.
pub effect ProbeRetirementEffect {
  ## Answer anything.
  fn probe_retirement_ask(k: Int) -> Int
}
""",
    "whitespace": "\n",
    # An unused `type` or `effect` is dropped before lowering, so on a tree
    # where ids are already derived those two edits move nothing at all and
    # satisfy criterion A vacuously. These two are the same declarations made
    # reachable, so the edit reaches Core and the criterion has something to
    # measure. Keep both: the vacuous pair is still the survey's list, and the
    # difference between the pair is itself the finding.
    "type_used": """
## Probe (retirement-probe.py): an unrelated type, reachable.
pub type ProbeRetirementUsedAdt =
  | ProbeRetirementUsedAlpha
  | ProbeRetirementUsedBeta(v: Int)

pub fn probe_retirement_make(x: Int) -> ProbeRetirementUsedAdt =
  ProbeRetirementUsedBeta(x)
""",
    "effect_used": """
## Probe (retirement-probe.py): an unrelated effect, reachable.
pub effect ProbeRetirementUsedEffect {
  ## Answer anything.
  fn probe_retirement_used_ask(k: Int) -> Int
}

pub fn probe_retirement_perform(k: Int) -> Int !ProbeRetirementUsedEffect =
  probe_retirement_used_ask(k)
""",
}

# The two modules K4's acceptance used. Both are early: everything downstream of
# them is checked after them, which is what makes a shared counter visible.
DEFAULT_TARGETS = ["std/list.dawn", "selfhost/src/front/token.dawn"]

COPY_IGNORE = shutil.ignore_patterns(".git", ".dawn", "node_modules")


def module_of(rel_path):
    """Hash-table key for a source path, spelled as the goldens spell it (`./x.core`)."""
    rel = str(rel_path)
    if rel.startswith("selfhost/src/"):
        stem = rel[len("selfhost/src/"):]
    elif rel.startswith("std/"):
        stem = rel
    else:
        raise SystemExit(f"retirement-probe: no module name rule for {rel}")
    return "./" + stem[:-len(".dawn")].replace("/", ".") + ".core"


# ---------------------------------------------------------------- hashing

def load_normalizer(path):
    """The filter under test, loaded from the tree rather than reimplemented.

    One definition, both readers: `selfhost-core-diff.sh:141` pipes each dump
    through this same file, so a probe with its own copy of the regex would be
    measuring something nobody runs.
    """
    spec = importlib.util.spec_from_file_location("core_normalize", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.normalize


def hash_dump(dump_dir, normalize):
    """(exact, normalized) hash tables, keyed by `./name.core` as the goldens are."""
    exact, norm = {}, {}
    for path in sorted(Path(dump_dir).glob("*.core")):
        text = path.read_text()
        key = "./" + path.name
        exact[key] = hashlib.sha256(text.encode()).hexdigest()
        norm[key] = hashlib.sha256(normalize(text).encode()).hexdigest()
    if not exact:
        raise SystemExit(f"retirement-probe: no dumps under {dump_dir}")
    return exact, norm


def parse_sha_file(path):
    table = {}
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        digest, name = re.split(r"\s+", line.strip(), maxsplit=1)
        table[name] = digest
    return table


# ------------------------------------------------------- verdict, pure part

def changed_modules(before, after):
    """Modules whose hash moved, appeared or vanished."""
    return {k for k in set(before) | set(after) if before.get(k) != after.get(k)}


def classify(base_exact, base_norm, var_exact, var_norm, expected):
    exact = changed_modules(base_exact, var_exact)
    norm = changed_modules(base_norm, var_norm)
    return {
        "exact": exact,
        "norm": norm,
        "expected": expected,
        "filtered": exact - norm,          # what normalization still hides
        "norm_only": norm - exact,         # should never happen; a filter that adds
        "collateral": exact - expected,
        "criterion_a": exact == norm,
        "criterion_b": exact <= expected,
    }


# ------------------------------------------------------------ running dawn

def dawn_env(seed_cache):
    env = dict(os.environ)
    if seed_cache:
        env["DAWN_SEED_CACHE"] = str(seed_cache)
    return env


def lower_selfhost(tree, dump_dir, seed_cache):
    """Run the same command `selfhost-core-diff.sh:131` runs, from the same cwd.

    The path stays relative on purpose: panic sites bake whatever path the
    driver was handed, so an absolute one would make every dump machine-local.
    """
    Path(dump_dir).mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["./bin/dawn", "__lower", "--dump", str(dump_dir), "selfhost"],
        cwd=tree, env=dawn_env(seed_cache), capture_output=True, text=True,
    )
    log = proc.stdout + proc.stderr
    if proc.returncode != 0 or ", 0 failed" not in log:
        sys.stderr.write(log[-4000:])
        raise SystemExit(f"retirement-probe: lowering failed in {tree}")
    return log


def copy_tree(src, dst):
    shutil.copytree(src, dst, ignore=COPY_IGNORE, symlinks=True)


# ------------------------------------------------------------------ driver

def run_probe(args):
    tree = Path(args.tree).resolve()
    if not (tree / "bin" / "dawn").exists():
        raise SystemExit(f"retirement-probe: {tree} is not a dawn checkout")

    seed_cache = args.seed_cache
    if seed_cache is None and (tree / ".dawn" / "seeds").is_dir():
        seed_cache = tree / ".dawn" / "seeds"

    normalizer = tree / "scripts" / "core-normalize.py"
    if not normalizer.exists():
        raise SystemExit(
            "retirement-probe: no scripts/core-normalize.py in the tree. "
            "If it has already been retired there is nothing left to measure."
        )
    normalize = load_normalizer(normalizer)

    workdir = Path(args.workdir).resolve() if args.workdir else Path(
        tempfile.mkdtemp(prefix="retirement-probe-"))
    workdir.mkdir(parents=True, exist_ok=True)
    keep = args.keep or args.workdir is not None

    edits = [args.only] if args.only else list(EDITS)
    targets = args.targets or DEFAULT_TARGETS

    print(f"tree        {tree}")
    print(f"workdir     {workdir}")
    print(f"seed cache  {seed_cache or '(none; bin/dawn will fetch)'}")
    print(f"targets     {', '.join(targets)}")
    print(f"edits       {', '.join(edits)}")
    print()

    try:
        t0 = time.monotonic()
        base = workdir / "base"
        if not base.exists():
            copy_tree(tree, base)
        base_dump = workdir / "dump-base"
        if not base_dump.exists():
            lower_selfhost(base, base_dump, seed_cache)
        base_exact, base_norm = hash_dump(base_dump, normalize)
        print(f"baseline    {len(base_exact)} modules, {time.monotonic() - t0:.1f}s")

        golden = tree / "scripts" / "core-golden" / "selfhost.sha"
        if golden.exists():
            same = parse_sha_file(golden) == base_exact
            print(f"            exact hashes {'match' if same else 'DIFFER FROM'} "
                  f"scripts/core-golden/selfhost.sha")
            if not same:
                print("            (the tree is not at its recorded golden; the"
                      " probe still works, it compares against itself)")
        print()

        results = []
        for target in targets:
            source = tree / target
            if not source.exists():
                raise SystemExit(f"retirement-probe: no such file {source}")
            name = module_of(target)
            if name not in base_exact:
                raise SystemExit(f"retirement-probe: {target} maps to {name}, "
                                 f"which is not one of the dumped modules")
            expected = {name}
            for edit in edits:
                tag = f"{target.replace('/', '_')}.{edit}"
                started = time.monotonic()
                variant = workdir / f"v-{tag}"
                if variant.exists():
                    shutil.rmtree(variant)
                subprocess.run(["cp", "-a", str(base), str(variant)], check=True)
                with (variant / target).open("a") as fh:
                    fh.write(EDITS[edit])
                dump = workdir / f"dump-{tag}"
                if dump.exists():
                    shutil.rmtree(dump)
                lower_selfhost(variant, dump, seed_cache)
                var_exact, var_norm = hash_dump(dump, normalize)
                verdict = classify(base_exact, base_norm, var_exact, var_norm, expected)
                verdict["target"] = target
                verdict["edit"] = edit
                verdict["seconds"] = time.monotonic() - started
                results.append(verdict)
                if not keep:
                    shutil.rmtree(variant)
                report_one(verdict)
        print()
        return report_all(results, args.expect_filtering)
    finally:
        if not keep and workdir.exists():
            shutil.rmtree(workdir, ignore_errors=True)


def short(names):
    return ", ".join(sorted(n[2:-len(".core")] for n in names)) or "(none)"


def report_one(v):
    print(f"--- {v['target']}  +{v['edit']}   ({v['seconds']:.1f}s)")
    print(f"    exact moved  {short(v['exact'])}")
    print(f"    norm  moved  {short(v['norm'])}")
    if v["criterion_a"]:
        print("    A ok    normalization filtered nothing")
    else:
        print(f"    A FAIL  normalization still hides: {short(v['filtered'])}")
        if v["norm_only"]:
            print(f"            normalization INVENTED a change in: {short(v['norm_only'])}")
    if v["criterion_b"]:
        print("    B ok    only the edited module moved")
    else:
        print(f"    B FAIL  unedited modules moved: {short(v['collateral'])}")
        print("            judge each: a real dependent on what this module"
              " exports is fine, anything else is K4/K5 residue")
    print()


def report_all(results, expect_filtering):
    a_bad = [v for v in results if not v["criterion_a"]]
    b_bad = [v for v in results if not v["criterion_b"]]
    total = sum(v["seconds"] for v in results)
    print(f"=== {len(results)} edits, {total:.1f}s of measurement")
    print(f"    criterion A (exact set == normalized set): "
          f"{len(results) - len(a_bad)}/{len(results)} ok")
    print(f"    criterion B (only the edited module moved): "
          f"{len(results) - len(b_bad)}/{len(results)} ok")

    if expect_filtering:
        if a_bad:
            print()
            print("POSITIVE CONTROL OK: normalization still filters here, so the"
                  " probe can tell the two states apart.")
            for v in a_bad:
                print(f"    {v['target']} +{v['edit']}: hides {short(v['filtered'])}")
            return 0
        print()
        print("POSITIVE CONTROL FAILED: normalization filtered nothing on a tree"
              " that was supposed to need it. The probe proves nothing; find out"
              " why before trusting a green run elsewhere.")
        return 1

    if not a_bad and not b_bad:
        print()
        print("RETIRABLE: on this tree, and for these edits, core-normalize.py is"
              " an identity on the question the goldens ask. Retire it only with"
              " a red positive control in hand (--expect-filtering on a pre-K4"
              " tree).")
        return 0
    print()
    if a_bad:
        print("NOT RETIRABLE: normalization is still doing work.")
    if b_bad:
        print("NOT CLEAN: unrelated modules moved. Per survey section 3 (b) step"
              " 4 this is a K4/K5 signal, not a reason to keep the filter.")
    return 1


# --------------------------------------------------------------- self-test

def selftest(tree):
    """The verdict must be able to go red. Synthetic inputs only, no compiler."""
    mods = ["./a.core", "./b.core", "./c.core"]
    base = {m: "h" + m for m in mods}
    expected = {"./a.core"}

    v = classify(base, base, base, base, expected)
    assert v["criterion_a"] and v["criterion_b"], v
    assert v["exact"] == set()

    edited = dict(base, **{"./a.core": "moved"})
    v = classify(base, base, edited, edited, expected)
    assert v["criterion_a"] and v["criterion_b"], v
    assert v["exact"] == {"./a.core"}

    # exact moves a module the filter hides: criterion A must go red.
    v = classify(base, base, dict(base, **{"./b.core": "moved"}), base, expected)
    assert not v["criterion_a"], v
    assert v["filtered"] == {"./b.core"}

    # both move an unedited module: A holds, B must go red.
    noisy = dict(base, **{"./a.core": "moved", "./c.core": "moved"})
    v = classify(base, base, noisy, noisy, expected)
    assert v["criterion_a"] and not v["criterion_b"], v
    assert v["collateral"] == {"./c.core"}

    # a filter that invents a change is caught too.
    v = classify(base, base, base, dict(base, **{"./b.core": "moved"}), expected)
    assert not v["criterion_a"] and v["norm_only"] == {"./b.core"}, v

    # And the literal negative control: feed the recorded normalized list where
    # the exact list belongs. The goldens differ on every module that carries a
    # generated name, so this must be loudly red.
    golden = Path(tree) / "scripts" / "core-golden"
    exact_file, norm_file = golden / "selfhost.sha", golden / "selfhost.norm.sha"
    if exact_file.exists() and norm_file.exists():
        g_exact = parse_sha_file(exact_file)
        g_norm = parse_sha_file(norm_file)
        v = classify(g_exact, g_norm, g_norm, g_norm, {"./std.list.core"})
        assert not v["criterion_a"], "feeding the normalized list as exact stayed green"
        assert not v["criterion_b"], v
        print(f"OK: golden-fed control is red on {len(v['filtered'])} modules")
    else:
        print("note: no recorded goldens next to this script; synthetic checks only")

    print("OK: every criterion can go red")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="Measure whether scripts/core-normalize.py still filters anything.")
    ap.add_argument("--tree", default=str(DEFAULT_TREE),
                    help="checkout to probe (never modified; default: this one)")
    ap.add_argument("--workdir", default=None,
                    help="where the throwaway copies live (default: a temp dir, removed)")
    ap.add_argument("--seed-cache", default=None, type=Path,
                    help="DAWN_SEED_CACHE for the copies (default: <tree>/.dawn/seeds)")
    ap.add_argument("--only", choices=sorted(EDITS),
                    help="run a single edit class")
    ap.add_argument("--targets", nargs="+", default=None,
                    help=f"source files to edit (default: {' '.join(DEFAULT_TARGETS)})")
    ap.add_argument("--expect-filtering", action="store_true",
                    help="positive control: exit 0 only if normalization DOES filter")
    ap.add_argument("--keep", action="store_true", help="keep the work directory")
    ap.add_argument("--self-test", action="store_true",
                    help="prove the verdict can go red, without running the compiler")
    args = ap.parse_args()

    if args.self_test:
        return selftest(args.tree)
    return run_probe(args)


if __name__ == "__main__":
    sys.exit(main())
