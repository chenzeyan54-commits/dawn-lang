#!/usr/bin/env python3
"""Derive the external gate run's command list from gates.yml at one commit.

Why this exists rather than a list: a maintainer running the gates outside
GitHub needs the SAME gate set CI would have run on that commit, and the only
definition of that set is .github/workflows/gates.yml at that commit. A copied
list drifts; this is the drift sweep-plan.py was written against for the
incremental family, and this parser covers every job instead of one family.
It reads the file out of git (`git show <sha>:...`), never the working tree,
so a run on sha X cannot be planned from a checkout that happens to be at Y.

Why it refuses so much: every construct it does not model is a construct whose
local meaning would be a guess. So the accepted shape is closed, and anything
outside it is an error before any job starts:

  * top-level keys: name, on, jobs
  * job keys: runs-on (ubuntu-latest), timeout-minutes, steps, needs, and
    `if: ${{ always() }}` on a job that has needs
  * step keys: name, run, uses, with, env
  * `${{ }}` expressions: `runner.temp` in env values and in `with.path`,
    `secrets.GITHUB_TOKEN` in the toolchain action's `github-token`, nothing
    else anywhere (and none at all inside `run:` text)
  * `uses:` references: exactly those in SUBSTITUTIONS, spelled with their
    version; a version bump is a new reference and is refused until someone
    reviews what the new version does

Each `uses:` step is replaced by an action the backend executes, named by a
replacement id. The id vocabulary is fixed here and copied into the bundle, so
a reader sees what each reference became.

The dawn-toolchain composite is substituted as a whole, which is only honest
while the composite is what the substitution assumes. Its action.yml is read
at the same commit and fingerprinted; a composite that grew a step, changed a
cache path or changed a default is refused rather than approximated.

Modes:
  gatesplan.py --sha SHA [--repo DIR]      print the plan as JSON
  gatesplan.py --self-test                  refusal cases and a line-scan
                                            cross-check of the parser
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

GATES_PATH = ".github/workflows/gates.yml"
TOOLCHAIN_ACTION = "./.github/actions/dawn-toolchain"
TOOLCHAIN_PATH = ".github/actions/dawn-toolchain/action.yml"

# uses reference -> (replacement id, accepted `with:` keys).
# The replacement ids are the whole vocabulary the bundle may carry in
# substitutions[].replacement; bundle.py imports this table.
SUBSTITUTIONS = {
    "actions/checkout@v4": ("tree-worktree", {"fetch-depth"}),
    TOOLCHAIN_ACTION: ("dawn-toolchain-local", {"github-token", "build"}),
    "actions/cache@v4": ("noop", {"path", "key", "restore-keys"}),
    "actions/upload-artifact@v4": (
        "artifact-store-local", {"name", "path", "if-no-files-found", "retention-days"}),
    "actions/download-artifact@v4": ("artifact-fetch-local", {"pattern", "path"}),
    "actions/setup-node@v4": (
        "node-host", {"node-version", "cache", "cache-dependency-path"}),
    "actions/setup-java@v4": ("jdk21-host", {"distribution", "java-version"}),
}

# Adjustments the local backend makes that are not `uses:` substitutions but
# still change what a step sees. They go into the bundle's substitution table
# under these subjects so they are as visible as the `uses:` rows.
ADJUSTMENTS = {
    "adjust:runner-temp": "per-job-directory",
    "adjust:tmpdir": "per-job-directory",
    "adjust:literal-tmp-paths": "machine-wide-lock",
    "adjust:playground-port": "free-port-per-run",
    "adjust:github-env-files": "per-step-files",
}

TOP_KEYS = {"name", True, "on", "jobs"}  # PyYAML reads the key `on` as True
JOB_KEYS = {"runs-on", "timeout-minutes", "steps", "needs", "if"}
STEP_KEYS = {"name", "run", "uses", "with", "env"}
EXPR = re.compile(r"\$\{\{\s*(.*?)\s*\}\}")
RUNNER_TEMP_EXPR = re.compile(r"\$\{\{\s*runner\.temp\s*\}\}")


class PlanError(Exception):
    """The plan cannot be derived without guessing; nothing may run."""


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout


def read_at(repo, sha, path):
    """(text, blob sha) of one file at one commit."""
    try:
        blob = git(repo, "rev-parse", f"{sha}:{path}").strip()
        text = git(repo, "cat-file", "blob", blob)
    except subprocess.CalledProcessError as error:
        raise PlanError(f"{path} is not readable at {sha}: {error.stderr.strip()}")
    return text, blob


def resolve_sha(repo, sha):
    try:
        return git(repo, "rev-parse", "--verify", f"{sha}^{{commit}}").strip()
    except subprocess.CalledProcessError:
        raise PlanError(f"not a commit: {sha}")


def _no_expr(where, value):
    if isinstance(value, str) and "${{" in value:
        raise PlanError(f"{where}: unsupported expression {value!r}")


def _env(where, env):
    """Step env with `${{ runner.temp }}` kept symbolic for the backend."""
    if env is None:
        return {}
    if not isinstance(env, dict):
        raise PlanError(f"{where}: env is not a mapping")
    out = {}
    for key, value in env.items():
        value = str(value)
        rest = RUNNER_TEMP_EXPR.sub("", value)
        _no_expr(f"{where} env {key}", rest)
        out[str(key)] = value
    return out


def check_toolchain_action(text):
    """Fingerprint the composite the dawn-toolchain substitution stands for.

    The substitution means: a local JDK 21 on JAVA_HOME and PATH, the seed
    cache restored from a shared cache, and `./bin/dawn --version` unless
    `build` is 'false'. It is only that while the composite is exactly the
    two setup-graalvm attempts, a retry note, the two caches and the build.
    """
    doc = yaml.safe_load(text)
    problems = []
    inputs = doc.get("inputs") or {}
    if set(inputs) != {"github-token", "java-version", "distribution", "build"}:
        problems.append(f"inputs are {sorted(inputs)}")
    defaults = {k: (v or {}).get("default") for k, v in inputs.items()}
    for key, want in (("java-version", "21"), ("distribution", "graalvm-community"),
                      ("build", "true")):
        if key in defaults and str(defaults[key]) != want:
            problems.append(f"input {key} defaults to {defaults[key]!r}, not {want!r}")
    runs = doc.get("runs") or {}
    if runs.get("using") != "composite":
        problems.append("not a composite action")
    uses = []
    run_steps = []
    for step in runs.get("steps") or []:
        if "uses" in step:
            uses.append(step["uses"])
            if step["uses"] == "actions/cache@v4":
                path = (step.get("with") or {}).get("path")
                if path not in (".dawn/seeds", "~/.cache/coursier"):
                    problems.append(f"a cache step now caches {path!r}")
        elif "run" in step:
            run_steps.append((str(step.get("if", "")).strip(), step["run"].strip()))
        else:
            problems.append(f"step {step.get('name')!r} is neither uses nor run")
    if sorted(uses) != sorted(["graalvm/setup-graalvm@v1"] * 2 + ["actions/cache@v4"] * 2):
        problems.append(f"uses references are {sorted(uses)}")
    build = [r for c, r in run_steps if r == "./bin/dawn --version"]
    notes = [r for c, r in run_steps
             if c == "steps.graalvm.outcome == 'failure'" and r.startswith('echo "::warning')]
    if len(build) != 1 or len(notes) != 1 or len(run_steps) != 2:
        problems.append(f"run steps are {[r.splitlines()[0] for _, r in run_steps]}")
    build_if = [c for c, r in run_steps if r == "./bin/dawn --version"]
    if build_if and build_if[0] != "inputs.build == 'true'":
        problems.append(f"the build step is guarded by {build_if[0]!r}")
    if problems:
        raise PlanError(
            "dawn-toolchain composite no longer matches its substitution: "
            + "; ".join(problems))


def parse(gates_text, action_text=None):
    """The plan: jobs in definition order, each with ordered actions.

    Each action is either
      {"kind": "run", "name", "command", "env"}
      {"kind": "use", "uses", "replacement", "with", "env"}
    """
    doc = yaml.safe_load(gates_text)
    if not isinstance(doc, dict):
        raise PlanError("gates.yml is not a mapping")
    extra = set(doc) - TOP_KEYS
    if extra:
        raise PlanError(f"unsupported top-level keys: {sorted(map(str, extra))}")
    jobs_doc = doc.get("jobs") or {}
    if not jobs_doc:
        raise PlanError("gates.yml has no jobs")
    jobs = []
    toolchain_seen = False
    for job_id, job in jobs_doc.items():
        where = f"job {job_id}"
        if not isinstance(job, dict):
            raise PlanError(f"{where} is not a mapping")
        extra = set(job) - JOB_KEYS
        if extra:
            raise PlanError(f"{where}: unsupported keys {sorted(extra)}")
        if job.get("runs-on") != "ubuntu-latest":
            raise PlanError(f"{where}: runs-on {job.get('runs-on')!r}")
        timeout = job.get("timeout-minutes")
        if not isinstance(timeout, int) or timeout <= 0:
            raise PlanError(f"{where}: timeout-minutes {timeout!r}")
        needs = job.get("needs") or []
        if isinstance(needs, str):
            needs = [needs]
        for need in needs:
            if need not in jobs_doc:
                raise PlanError(f"{where}: needs unknown job {need!r}")
        if "if" in job:
            cond = str(job["if"]).strip()
            if not needs or EXPR.sub(lambda m: m.group(1), cond).strip() != "always()":
                raise PlanError(f"{where}: unsupported job condition {cond!r}")
        actions = []
        for index, step in enumerate(job.get("steps") or []):
            swhere = f"{where} step {index + 1}"
            if not isinstance(step, dict):
                raise PlanError(f"{swhere} is not a mapping")
            extra = set(step) - STEP_KEYS
            if extra:
                raise PlanError(f"{swhere}: unsupported keys {sorted(extra)}")
            has_run, has_uses = "run" in step, "uses" in step
            if has_run == has_uses:
                raise PlanError(f"{swhere}: needs exactly one of run/uses")
            name = step.get("name")
            _no_expr(f"{swhere} name", name)
            env = _env(swhere, step.get("env"))
            if has_run:
                if "with" in step:
                    raise PlanError(f"{swhere}: `with` on a run step")
                command = step["run"]
                if not isinstance(command, str) or not command.strip():
                    raise PlanError(f"{swhere}: empty run")
                _no_expr(f"{swhere} run", command)
                actions.append({"kind": "run", "name": name, "command": command, "env": env})
                continue
            uses = step["uses"]
            if uses not in SUBSTITUTIONS:
                raise PlanError(
                    f"{swhere}: `uses: {uses}` has no substitution; add a reviewed "
                    f"row to SUBSTITUTIONS rather than skipping it")
            replacement, accepted = SUBSTITUTIONS[uses]
            with_ = step.get("with") or {}
            extra = set(with_) - accepted
            if extra:
                raise PlanError(f"{swhere}: {uses} with unsupported inputs {sorted(extra)}")
            clean = {}
            for key, value in with_.items():
                value = str(value)
                if uses == TOOLCHAIN_ACTION and key == "github-token":
                    if value.replace(" ", "") != "${{secrets.GITHUB_TOKEN}}":
                        raise PlanError(f"{swhere}: github-token is {value!r}")
                    continue  # never forwarded: the local backend has no token
                if key == "path":
                    _no_expr(f"{swhere} with.path", RUNNER_TEMP_EXPR.sub("", value))
                else:
                    _no_expr(f"{swhere} with.{key}", value)
                clean[key] = value
            if uses == TOOLCHAIN_ACTION:
                toolchain_seen = True
                if clean.get("build", "true") not in ("true", "false"):
                    raise PlanError(f"{swhere}: build is {clean['build']!r}")
            if uses == "actions/setup-java@v4":
                if clean.get("java-version") != "21":
                    raise PlanError(f"{swhere}: setup-java asks for {clean.get('java-version')!r}")
            actions.append({"kind": "use", "name": name, "uses": uses,
                            "replacement": replacement, "with": clean, "env": env})
        if not actions:
            raise PlanError(f"{where}: no steps")
        jobs.append({"id": job_id, "timeout_minutes": timeout, "needs": needs,
                     "actions": actions})
    if toolchain_seen:
        if action_text is None:
            raise PlanError("the toolchain composite was not read at this commit")
        check_toolchain_action(action_text)
    return jobs


def run_commands(jobs):
    """The (job, command) multiset of every run step, in gates.yml order."""
    return [(job["id"], action["command"])
            for job in jobs for action in job["actions"] if action["kind"] == "run"]


def substitution_rows(jobs):
    """Distinct (uses, replacement) rows in first-use order, then adjustments."""
    rows, seen = [], set()
    for job in jobs:
        for action in job["actions"]:
            if action["kind"] == "use" and action["uses"] not in seen:
                seen.add(action["uses"])
                rows.append({"subject": action["uses"], "replacement": action["replacement"]})
                if action["uses"] == TOOLCHAIN_ACTION:
                    # the composite's own caches are the other half of this row
                    seen.add("actions/cache@v4")
                    rows.append({"subject": "actions/cache@v4", "replacement": "noop"})
    for subject, replacement in ADJUSTMENTS.items():
        rows.append({"subject": subject, "replacement": replacement})
    return rows


def plan_at(repo, sha):
    """Everything the runner needs from one commit, read out of git."""
    tree = resolve_sha(repo, sha)
    gates_text, gates_blob = read_at(repo, tree, GATES_PATH)
    try:
        action_text, _ = read_at(repo, tree, TOOLCHAIN_PATH)
    except PlanError:
        action_text = None
    jobs = parse(gates_text, action_text)
    return {"tree": tree, "gates_blob": gates_blob, "gates_text": gates_text, "jobs": jobs}


# ------------------------------------------------------------------ self-test

RUN_KEY_LINE = re.compile(r"^\s*(?:-\s+)?run:", re.M)


def line_scan_count(text):
    """Independent count of `run:` keys, so the YAML walk cannot lose one."""
    return sum(1 for line in text.splitlines()
               if not line.lstrip().startswith("#") and RUN_KEY_LINE.match(line))


def self_test(repo):
    failures = []
    ok_action = (Path(__file__).resolve().parents[2] / TOOLCHAIN_PATH).read_text()

    def job(steps, **extra):
        body = {"runs-on": "ubuntu-latest", "timeout-minutes": 5, "steps": steps}
        body.update(extra)
        return body

    def doc(jobs, **top):
        d = {"name": "gates", "on": {"workflow_call": None}, "jobs": jobs}
        d.update(top)
        return yaml.safe_dump(d, sort_keys=False)

    good = doc({"a": job([{"uses": "actions/checkout@v4"},
                          {"uses": TOOLCHAIN_ACTION,
                           "with": {"github-token": "${{ secrets.GITHUB_TOKEN }}"}},
                          {"name": "x", "run": "echo a",
                           "env": {"D": "${{ runner.temp }}/d"}}]),
                "b": job([{"run": "echo b"}], needs=["a"], **{"if": "${{ always() }}"})})
    try:
        plan = parse(good, ok_action)
        if run_commands(plan) != [("a", "echo a"), ("b", "echo b")]:
            failures.append(f"accepted plan has the wrong commands: {run_commands(plan)}")
    except PlanError as error:
        failures.append(f"refused a supported document: {error}")

    refused = {
        "unknown uses": doc({"a": job([{"uses": "actions/checkout@v5"}])}),
        "unknown third-party uses": doc({"a": job([{"uses": "someone/thing@v1"}])}),
        "step if": doc({"a": job([{"run": "echo", "if": "success()"}])}),
        "step shell": doc({"a": job([{"run": "echo", "shell": "sh"}])}),
        "working-directory": doc({"a": job([{"run": "echo", "working-directory": "x"}])}),
        "continue-on-error": doc({"a": job([{"run": "echo", "continue-on-error": True}])}),
        "job strategy": doc({"a": job([{"run": "echo"}], strategy={"matrix": {"x": [1]}})}),
        "job services": doc({"a": job([{"run": "echo"}], services={})}),
        "job env": doc({"a": job([{"run": "echo"}], env={"A": "1"})}),
        "job condition other than always": doc({
            "a": job([{"run": "echo"}]),
            "b": job([{"run": "echo"}], needs=["a"], **{"if": "${{ success() }}"})}),
        "job condition without needs": doc({"a": job([{"run": "echo"}], **{"if": "always()"})}),
        "needs unknown job": doc({"a": job([{"run": "echo"}], needs=["zz"])}),
        "other runner": doc({"a": dict(job([{"run": "echo"}]), **{"runs-on": "windows-latest"})}),
        "expression in run": doc({"a": job([{"run": "echo ${{ github.sha }}"}])}),
        "other expression in env": doc({"a": job([{"run": "echo", "env": {"X": "${{ github.ref }}"}}])}),
        "unknown with input": doc({"a": job([{"uses": "actions/checkout@v4",
                                               "with": {"submodules": "true"}}])}),
        "setup-java other version": doc({"a": job([{"uses": "actions/setup-java@v4",
                                                     "with": {"java-version": "17"}}])}),
        "top-level env": doc({"a": job([{"run": "echo"}])}, env={"A": "1"}),
        "top-level defaults": doc({"a": job([{"run": "echo"}])}, defaults={"run": {"shell": "sh"}}),
        "run and uses": doc({"a": job([{"run": "echo", "uses": "actions/checkout@v4"}])}),
        "empty job": doc({"a": job([])}),
    }
    for label, text in refused.items():
        try:
            parse(text, ok_action)
        except PlanError:
            continue
        failures.append(f"accepted: {label}")

    drifted = ok_action.replace("path: .dawn/seeds", "path: .dawn")
    if drifted == ok_action:
        failures.append("the toolchain drift fixture did not change the action")
    else:
        try:
            parse(good, drifted)
            failures.append("accepted a drifted toolchain composite")
        except PlanError:
            pass

    head = resolve_sha(repo, "HEAD")
    live = plan_at(repo, head)
    parsed = len(run_commands(live["jobs"]))
    scanned = line_scan_count(live["gates_text"])
    if parsed != scanned:
        failures.append(f"HEAD gates.yml: parser found {parsed} run steps, line scan {scanned}")

    for line in failures:
        print(f"FAIL gatesplan self-test: {line}", file=sys.stderr)
    if failures:
        return 1
    print(f"OK: gatesplan self-test, {len(refused) + 1} refusals, "
          f"{len(live['jobs'])} jobs and {parsed} run steps at {head[:12]}")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[2]))
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--sha")
    group.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test(args.repo)
    try:
        plan = plan_at(args.repo, args.sha)
    except PlanError as error:
        print(f"gatesplan: {error}", file=sys.stderr)
        return 2
    plan.pop("gates_text")
    json.dump(plan, sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
