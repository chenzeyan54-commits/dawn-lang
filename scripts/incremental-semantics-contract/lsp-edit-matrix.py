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
            if args.expect_reuse and label in {"whitespace", "body", "inferred-signature", "reorder"}:
                if len(counts) != 1 or not counts[0]["observed"] or counts[0]["counts"]["reused_bodies"] <= 0:
                    raise RuntimeError(f"{label}: no observed body reuse")
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
    main()
