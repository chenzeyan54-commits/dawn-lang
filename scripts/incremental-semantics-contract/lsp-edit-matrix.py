#!/usr/bin/env python3
"""Exercise real synthetic document revisions; this is not a latency benchmark.

Complete diagnostics and query replies can be compared between independently
configured servers. Execution counts are kept separately from semantic output.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

from lsp_stats import decode

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/lsp-workspace-contract"))
from workspace import LspClient, did_open, did_change, position


def revisions(size):
    functions = [f"fn value_{i}(x: Int) -> Int = x + {i}\n" for i in range(size)]
    prefix = "fn provider() = 1\nfn dependent() = provider()\n"
    suffix = "pub fn main() -> Unit !io = println(to_string(value_0(1)))\n"
    baseline = prefix + "".join(functions) + suffix
    return [
        ("initial", baseline, False),
        ("whitespace", "# moved declarations\n\n" + baseline, False),
        ("body", baseline.replace("x + 0\n", "x + 17\n", 1), False),
        ("inferred-signature", baseline.replace("provider() = 1", "provider() = false", 1), False),
        ("reorder", prefix + "".join(reversed(functions)) + suffix, False),
        ("delete", prefix + "".join(functions[:-1]) + suffix, False),
        ("insert", baseline + "fn extra(x: Int) -> Int = x + 7\n", False),
        ("error", baseline + "pub fn benchmark_type_error() -> Int = false\n", True),
        ("recovery", baseline, False),
        ("identical", baseline, False),
    ]


def expected_counts(size):
    # Each revision is compared to the preceding revision, not the baseline.
    # main reaches IO and stays cold. Signature edits also reset value_0's body;
    # reorder restores provider's inferred signature. Errors discard retention.
    return dict(zip((label for label, _, _ in revisions(size)), (
        (size + 3, 0, 0, 0), (1, size + 2, 0, 0), (2, size + 1, 0, 0),
        (4, size - 1, 0, 1), (3, size, 0, 1), (1, size + 1, 0, 0),
        (3, size + 1, 0, 0), (2, size + 2, 0, 0), (size + 3, 0, 0, 0),
        (0, 0, 1, 0),
    )))


def validate_counts(counts, expected):
    if len(counts) != 1 or not counts[0]["observed"] or counts[0]["scope"] != "standalone":
        raise RuntimeError("expected one observed standalone analysis")
    actual = tuple(counts[0]["counts"][field] for field in (
        "checked_bodies", "reused_bodies", "reused_modules", "cold_rejected_bodies"))
    if actual != expected:
        raise RuntimeError(f"analysis count mismatch: {actual} != {expected}")


def selftest():
    fields = ("checked_bodies", "reused_bodies", "reused_modules", "cold_rejected_bodies")
    for expected in expected_counts(20).values():
        good = {"observed": True, "scope": "standalone", "counts": dict(zip(fields, expected))}
        validate_counts([good], expected)
        bad = {**good, "counts": {**good["counts"], "reused_bodies": expected[1] + 1}}
        for invalid in ([], [good, good], [{**good, "observed": False}], [bad]):
            try:
                validate_counts(invalid, expected)
            except RuntimeError:
                continue
            raise AssertionError("count oracle accepted invalid observation")
    print("OK: ten edit censuses and forty count-oracle rejection cases")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--functions", type=int, default=1000)
    parser.add_argument("--expect-reuse", action="store_true")
    parser.add_argument("--compare", type=Path, help="prior matrix output directory")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if args.functions < 2 or not command:
        parser.error("require at least two functions and a server command")
    args.output.mkdir(parents=True, exist_ok=False)
    cases = revisions(args.functions)
    (args.output / "metadata.json").write_text(json.dumps({
        "command": command, "functions": args.functions, "synthetic": True,
        "timing_evidence": False,
        "sources": {label: hashlib.sha256(text.encode()).hexdigest() for label, text, _ in cases},
    }, indent=2) + "\n")
    uri = "untitled:body-edit-matrix"
    client = LspClient(command, ROOT)
    rows = []
    try:
        client.initialize()
        for index, (label, text, expected_error) in enumerate(cases):
            mark, stderr_mark = client.mark(), len(client.stderr_text())
            version = index + 1
            client.send(did_open(uri, text, version) if index == 0 else did_change(uri, text, version))
            frames = client.barrier(mark)
            publishes = [frame["params"] for frame in frames
                         if frame.get("method") == "textDocument/publishDiagnostics"]
            own = [item for item in publishes if item.get("uri") == uri]
            if not own or own[-1].get("version") != version:
                raise RuntimeError(f"{label}: missing current-version publication")
            diagnostics = own[-1].get("diagnostics", [])
            if expected_error:
                if not any("benchmark_type_error" in item.get("message", "") for item in diagnostics):
                    raise RuntimeError(f"{label}: missing injected diagnostic")
            elif any(item.get("diagnostics") for item in publishes):
                raise RuntimeError(f"{label}: unexpected diagnostics: {publishes!r}")
            replies = {}
            for needle in ("provider()\n", "value_0(1)"):
                point = position(text, needle)
                replies[needle] = {method: client.result("textDocument/" + method, {
                    "textDocument": {"uri": uri}, "position": point})
                    for method in ("hover", "definition", "completion")}
                if replies[needle]["hover"] is None or not replies[needle]["definition"]:
                    raise RuntimeError(f"{label}: unresolved query target {needle!r}")
            counts = [decode(line) for line in client.stderr_text()[stderr_mark:].splitlines()
                      if line.startswith("LSP_BODY_STATS\t")]
            if args.expect_reuse:
                validate_counts(counts, expected_counts(args.functions)[label])
            rows.append({"label": label, "version": version, "diagnostics": publishes,
                         "replies": replies, "analysis_counts": counts})
            (args.output / "samples.json").write_text(json.dumps(rows, indent=2) + "\n")
        client.shutdown_exit()
    finally:
        (args.output / "stderr.txt").write_text(client.stderr_text())
        client.close()
    semantic = [{key: value for key, value in row.items() if key != "analysis_counts"} for row in rows]
    (args.output / "semantic.json").write_text(json.dumps(semantic, indent=2) + "\n")
    if args.compare:
        reference_meta = json.loads((args.compare / "metadata.json").read_text())
        if reference_meta["sources"] != json.loads((args.output / "metadata.json").read_text())["sources"]:
            raise RuntimeError("comparison source revisions differ")
        if json.loads((args.compare / "semantic.json").read_text()) != semantic:
            raise RuntimeError("cold/configured protocol results differ")
    print(f"OK: {len(rows)} real protocol revisions; synthetic {args.functions}-function corpus; no timing claim")


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        selftest()
    else:
        main()
