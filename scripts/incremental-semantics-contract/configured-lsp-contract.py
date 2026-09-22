#!/usr/bin/env python3
"""Compose fresh protocol/count positives before accepting exact reparse failures.

Keep the existing builder, matrices and ASM observer authoritative. Separate
complete semantic runs from intentionally early count rejection, so a broken
compiler, unrelated exception or partial matrix cannot satisfy a control.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SCOPES = ("standalone", "project")
JVM = ("-Xss512m", "-Xmx2g", "-XX:+UseSerialGC",
       "--add-exports=java.base/jdk.internal.vm=ALL-UNNAMED")


def selected(suite):
    if suite == "all":
        return SCOPES
    if suite not in SCOPES:
        raise ValueError("unknown suite")
    return (suite,)


def plan(suite):
    steps = [("build", mode, observed, None)
             for mode in ("Cold", "PreparedBodies") for observed in (False, True)]
    for scope in selected(suite):
        for variant in ("cold-plain", "prepared-plain", "cold-stats", "prepared-stats",
                        "cold-entries", "prepared-entries"):
            steps.append(("matrix", scope, variant))
        steps.extend((("build", "PreparedBodies", True, scope),
                      ("matrix", scope, "reparse-semantic"),
                      ("matrix", scope, "reparse-rejected")))
    return steps


def rejection(scope):
    if scope == "standalone":
        return "RuntimeError: initial: parse/index/projection counts [2, 2, 2] != [1, 1, 1]"
    if scope == "project":
        return "RuntimeError: provider-body: project parse/index/projection counts [4, 4, 4] != [2, 2, 2]"
    raise ValueError("unknown scope")


def verify_exit(status, output, expected=None, timed_out=False):
    if timed_out:
        raise RuntimeError("subprocess timeout is not contract evidence")
    if expected is None:
        if status != 0:
            raise RuntimeError(f"positive subprocess exited {status}")
        return
    lines = output.strip().splitlines()
    forbidden = ("VerifyError", "LinkageError", "ClassNotFound", "NoClassDefFound",
                 "ExceptionInInitializerError", "Exception in thread", "HARNESS_",
                 "assertion failed", "panic:", "TimeoutExpired", "SyntaxError",
                 "During handling of the above exception", "direct cause of the following")
    if (status != 1 or not lines or lines[-1] != expected or
            lines[0] != "Traceback (most recent call last):" or
            any(line and not line.startswith("  ") for line in lines[1:-1]) or
            sum(line.startswith("RuntimeError:") for line in lines) != 1 or
            sum(line == "Traceback (most recent call last):" for line in lines) != 1 or
            any(marker in output for marker in forbidden)):
        raise RuntimeError("control did not fail solely at its exact parse-count assertion")


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def log_tail(path):
    # Fixed synthetic fixtures only. Bound both very long lines and line count;
    # never dump the complete compiler tree or artifact into CI output.
    with path.open("rb") as stream:
        stream.seek(max(0, path.stat().st_size - 8192))
        text = stream.read(8192).decode("utf-8", errors="replace")
    return "\n".join(text.splitlines()[-40:])[-4000:]


def stop_tree(proc):
    # LspClient gives its server a new session. A process-group kill alone
    # would leave that server alive after a matrix timeout. Resolve only this
    # process's actual descendants, never matching command names globally.
    rows = subprocess.check_output(["ps", "-eo", "pid=,ppid="], text=True)
    edges = [tuple(map(int, line.split())) for line in rows.splitlines()]
    descendants = {proc.pid}
    while True:
        found = descendants | {pid for pid, parent in edges if parent in descendants}
        if found == descendants:
            break
        descendants = found
    for pid in sorted(descendants, reverse=True):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    proc.wait()


class Run:
    def __init__(self, output, timeout):
        self.output, self.timeout, self.commands = output, timeout, []

    def execute(self, label, command, expected=None, detail_log=None):
        log = self.output / (label + ".log")
        started = time.monotonic()
        timed_out = False
        with log.open("w") as stream:
            proc = subprocess.Popen([str(x) for x in command], cwd=ROOT,
                                    stdout=stream, stderr=subprocess.STDOUT,
                                    start_new_session=True)
            try:
                status = proc.wait(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                stop_tree(proc)
                status = proc.returncode
        elapsed = time.monotonic() - started
        self.commands.append({"label": label, "command": [str(x) for x in command],
                              "status": status, "timeout": timed_out, "seconds": elapsed,
                              "log": log.name, "log_sha256": digest(log),
                              "expected_rejection": expected})
        save(self.output / "commands.json", self.commands)
        try:
            verify_exit(status, log.read_text(), expected, timed_out)
        except (RuntimeError, UnicodeError):
            print(f"FAIL {label}: status={status} timeout={timed_out} log={log} "
                  f"sha256={self.commands[-1]['log_sha256']}", file=sys.stderr, flush=True)
            for evidence in (log, detail_log):
                if evidence is not None and evidence.is_file():
                    print(f"Bounded log tail: {evidence}\n{log_tail(evidence)}",
                          file=sys.stderr, flush=True)
            raise
        print(f"PASS {label}: {elapsed:.2f}s", flush=True)


def matrix_name(scope):
    return "lsp-edit-matrix.py" if scope == "standalone" else "lsp-project-matrix.py"


def check_observation(sample, scope, variant):
    counts = sample["analysis_counts"]
    if variant.endswith("-plain"):
        if counts or sample["parse_counts"] is not None:
            raise RuntimeError("plain artifact unexpectedly emitted private observations")
    else:
        observed = not (scope == "standalone" and variant.startswith("cold-"))
        if (len(counts) != 1 or counts[0]["scope"] != scope or
                counts[0]["observed"] != observed):
            raise RuntimeError("private observer is absent or reports the wrong scope/presence")
        if variant.endswith("-stats") and sample["parse_counts"] is not None:
            raise RuntimeError("stats-only artifact unexpectedly emitted method-entry observations")


def check_complete(directory, scope, variant):
    samples = json.loads((directory / "samples.json").read_text())
    semantic = json.loads((directory / "semantic.json").read_text())
    labels = (["initial", "whitespace", "body", "inferred-signature", "reorder",
               "delete", "insert", "error", "recovery", "identical"] if scope == "standalone"
              else ["provider-body", "provider-signature", "consumer-recovery", "provider-error",
                    "provider-recovery", "provider-move", "close-provider", "reopen-provider"])
    if [row["label"] for row in samples] != labels or [row["label"] for row in semantic] != labels:
        raise RuntimeError("matrix did not complete every expected revision")
    for sample in samples:
        check_observation(sample, scope, variant)


def verify_workflow(text):
    script = "scripts/incremental-semantics-contract/configured-lsp-contract.py"
    actual = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#") or script not in line:
            continue
        if line.startswith("run: "):
            line = line[5:]
        actual.append(shlex.split(line))
    expected = [["python3", script, "--self-test"]] + [
        ["python3", script, "--suite", scope, "--output",
         "$RUNNER_TEMP/configured-lsp-" + scope] for scope in SCOPES]
    if sorted(actual) != sorted(expected):
        raise RuntimeError("configured LSP CI must run self-test and both independent suites exactly once")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=ROOT)
    parser.add_argument("--suite", choices=("all", *SCOPES), default="all")
    parser.add_argument("--functions", type=int, default=20)
    parser.add_argument("--timeout", type=int, default=600, help="per-subprocess limit in seconds")
    parser.add_argument("--asm-jar", type=Path)
    args = parser.parse_args()
    if args.functions < 2 or args.timeout < 1:
        parser.error("require at least two functions and a positive timeout")
    java_home = os.environ.get("JAVA_HOME")
    if not java_home:
        parser.error("set JAVA_HOME to JDK 21 or newer")
    java, javac = (Path(java_home) / "bin" / name for name in ("java", "javac"))
    for tool in (java, javac):
        if not tool.is_file() or not os.access(tool, os.X_OK):
            parser.error(f"missing executable {tool}")
    os.environ["PATH"] = str(Path(java_home) / "bin") + os.pathsep + os.environ.get("PATH", "")
    asm = args.asm_jar
    if asm is None:
        jars = sorted((Path.home() / ".cache/coursier/v1/https").glob(
            "**/org/ow2/asm/asm/9.7.1/asm-9.7.1.jar"))
        if not jars:
            parser.error("ASM 9.7.1 is not cached; provide --asm-jar")
        asm = jars[0]
    asm = asm.resolve(strict=True)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    run = Run(output, args.timeout)
    source = args.source.resolve(strict=True)
    metadata = {"suite": args.suite, "functions": args.functions, "source": str(source),
                "fresh_builds": True, "timing_evidence": False, "complete": False,
                "jdk": str(Path(java_home).resolve()), "asm_sha256": digest(asm),
                "tools": {name: digest(HERE / name) for name in (
                    Path(__file__).name, "lsp-configured.py", "SourceParseCounts.java",
                    "lsp-edit-matrix.py", "lsp-project-matrix.py", "lsp_stats.py")},
                "plan": plan(args.suite)}
    save(output / "metadata.json", metadata)
    run.execute("java-version", [java, "-version"])
    run.execute("javac-version", [javac, "-version"])
    for label in ("java", "javac"):
        version = (output / (label + "-version.log")).read_text()
        found = re.search(r'(?:version\s+"?|javac\s+)(\d+)', version)
        if not found or int(found[1]) < 21:
            raise RuntimeError("JDK 21 or newer is required")
    classes = output / "observer"
    classes.mkdir()
    run.execute("observer-build", [javac, "--release", "21", "-cp", asm, "-d", classes,
                                   HERE / "SourceParseCounts.java"])
    artifacts, complete = {}, set()
    original_sources = None
    for step in plan(args.suite):
        if step[0] == "build":
            _, mode, observed, control = step
            name = ("reparse-" + control if control else
                    ("cold" if mode == "Cold" else "prepared") + ("-stats" if observed else "-plain"))
            destination = output / name
            command = [sys.executable, HERE / "lsp-configured.py", "--source", source,
                       "--output", destination, "--mode", mode]
            if not observed:
                command.append("--uninstrumented")
            if control:
                command.extend(("--reparse-control", control))
            run.execute("build-" + name, command, detail_log=destination / "build.log")
            artifact = destination / "compiler.jar"
            built = json.loads((destination / "metadata.json").read_text())
            if (built["compiler_sha256"] != digest(artifact) or built["mode"] != mode or
                    built["instrumented"] != observed or built["reparse_control"] != control):
                raise RuntimeError("builder artifact provenance differs from requested policy")
            if original_sources is None:
                original_sources = built["sources"]
            elif original_sources != built["sources"]:
                raise RuntimeError("source inputs changed between private artifact builds")
            artifacts[name] = artifact
            metadata["artifacts"] = {str(path.relative_to(output)): digest(path)
                                     for path in sorted(output.rglob("*"))
                                     if path.is_file() and path.suffix in {".jar", ".class"}}
            save(output / "metadata.json", metadata)
            continue
        _, scope, variant = step
        label = scope + "-" + variant
        counted = variant.endswith("entries") or variant.startswith("reparse-")
        name = ("reparse-" + scope if variant.startswith("reparse-") else
                variant.replace("-entries", "-stats"))
        jar = artifacts[name]
        for path, expected_hash in metadata["artifacts"].items():
            if digest(output / path) != expected_hash:
                raise RuntimeError("built launch artifact changed before matrix execution")
        command = [java, *JVM]
        if counted:
            command.extend(("-cp", str(classes) + os.pathsep + str(asm),
                            "contract.SourceParseCounts", "--lsp", jar))
        else:
            command.extend(("-jar", jar, "lsp"))
        destination = output / ("matrix-" + label)
        matrix = [sys.executable, HERE / matrix_name(scope), "--output", destination]
        if scope == "standalone":
            matrix.extend(("--functions", str(args.functions)))
        if variant != "cold-plain":
            if (scope, "cold-plain") not in complete:
                raise RuntimeError("independent cold positive has not completed")
            matrix.extend(("--compare", output / ("matrix-" + scope + "-cold-plain")))
        if variant in ("prepared-stats", "prepared-entries", "reparse-semantic", "reparse-rejected"):
            matrix.append("--expect-reuse")
        expected = None
        if counted and variant != "reparse-semantic":
            matrix.extend(("--expect-parse-counts", "cold" if variant == "cold-entries" else "prepared"))
        if variant == "reparse-rejected":
            if (scope, "reparse-semantic") not in complete:
                raise RuntimeError("control's full semantic/body-count positive has not completed")
            expected = rejection(scope)
        run.execute(label, [*matrix, "--", *command], expected)
        if expected is None:
            check_complete(destination, scope, variant)
            complete.add((scope, variant))
    metadata["artifacts"] = {str(path.relative_to(output)): digest(path)
                             for path in sorted(output.rglob("*"))
                             if path.is_file() and path.suffix in {".jar", ".class"}}
    metadata["complete"] = True
    save(output / "metadata.json", metadata)
    print(f"OK: configured LSP {args.suite} contract; fresh builds, full equivalence and exact controls")


def selftest():
    with tempfile.TemporaryDirectory(prefix="configured-lsp-log-tail-") as directory:
        path = Path(directory) / "sample.log"
        for content in (b"", b"x" * 20000, b"row\n" * 2000, b"\xff" * 10000):
            path.write_bytes(content)
            tail = log_tail(path)
            assert len(tail) <= 4000 and len(tail.splitlines()) <= 40
    print("OK: four bounded failure-log tail cases")
    all_steps = plan("all")
    assert len(all_steps) == 22
    for scope in SCOPES:
        steps = plan(scope)
        assert len(steps) == 13
        assert steps[:4] == all_steps[:4]
        assert steps[4:] == [step for step in all_steps[4:] if scope in step]
        variants = [step[2] for step in steps if step[0] == "matrix"]
        assert variants == ["cold-plain", "prepared-plain", "cold-stats", "prepared-stats",
                            "cold-entries", "prepared-entries", "reparse-semantic", "reparse-rejected"]
        expected = rejection(scope)
        good = "Traceback (most recent call last):\n  File \"matrix.py\", line 1\n" + expected + "\n"
        verify_exit(0, "positive")
        verify_exit(1, good, expected)
        invalid = [(0, good, False), (2, good, False), (-9, good, False),
                   (1, good, True), (1, "compile failed", False),
                   (1, good.replace("[4, 4, 4]", "[5, 5, 5]").replace("[2, 2, 2]", "[3, 3, 3]"), False),
                   (1, good + "RuntimeError: unrelated error\n", False),
                   (1, "VerifyError\n" + good, False), (1, "HARNESS_TIMEOUT\n" + good, False),
                   (1, "panic: unrelated\n" + good, False), (1, expected, False),
                   (1, good + good, False), (1, "ClassNotFoundException\n" + good, False),
                   (1, "error: compile failed\n" + good, False),
                   (1, good.replace("  File", "unrelated error\n  File"), False)]
        for status, output, timed_out in invalid:
            try:
                verify_exit(status, output, expected, timed_out)
            except RuntimeError:
                continue
            raise AssertionError("unrelated failure accepted as a parse control")
    for status, timed_out in ((1, False), (0, True), (-9, False)):
        try:
            verify_exit(status, "", timed_out=timed_out)
        except RuntimeError:
            continue
        raise AssertionError("invalid positive accepted")
    plain = {"analysis_counts": [], "parse_counts": None}
    cold = {"analysis_counts": [{"scope": "standalone", "observed": False}], "parse_counts": None}
    check_observation(plain, "standalone", "cold-plain")
    check_observation(cold, "standalone", "cold-stats")
    for row, scope, variant in ((plain, "standalone", "cold-stats"),
                                (cold, "project", "cold-stats"),
                                (cold, "standalone", "prepared-stats"),
                                (cold, "standalone", "cold-plain"),
                                ({**cold, "parse_counts": [1, 0, 0]}, "standalone", "cold-stats")):
        try:
            check_observation(row, scope, variant)
        except RuntimeError:
            continue
        raise AssertionError("wrong private observation accepted")
    print("OK: independent suite plans, complete control ordering, and 38 rejection cases")
    script = "scripts/incremental-semantics-contract/configured-lsp-contract.py"
    lines = [f"python3 {script} --self-test"] + [
        f'run: python3 {script} --suite {scope} --output "$RUNNER_TEMP/configured-lsp-{scope}"'
        for scope in SCOPES]
    verify_workflow("\n".join(lines))
    for broken in (lines[:-1], lines + [lines[-1]],
                   [line.replace("--suite project", "--suite standalone") for line in lines],
                   [line.replace("--suite project", "--suite all") for line in lines],
                   [line.replace("--suite project", "--functions 2 --suite project") for line in lines]):
        try:
            verify_workflow("\n".join(broken))
        except RuntimeError:
            continue
        raise AssertionError("invalid configured LSP CI inventory accepted")
    verify_workflow((ROOT / ".github/workflows/gates.yml").read_text())
    print("OK: configured LSP CI inventory and five missing/duplicate/changed-suite controls")


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        selftest()
    else:
        main()
