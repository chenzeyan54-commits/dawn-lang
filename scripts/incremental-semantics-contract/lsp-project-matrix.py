#!/usr/bin/env python3
"""Compare real cross-module overlay revisions without changing fixture files.

The fixed fixture separates provider implementation and signature changes from
an unrelated consumer body. This validates protocol behavior, not latency.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

from lsp_stats import decode

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/lsp-workspace-contract"))
from workspace import LspClient, did_open, did_change, did_close, position


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--expect-reuse", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("provide a server command")
    fixture = Path(__file__).resolve().parent / "project-edit-fixture"
    paths = {name: fixture / "src" / (name + ".dawn") for name in ("lib", "main")}
    texts = {name: path.read_text() for name, path in paths.items()}
    uris = {name: path.as_uri() for name, path in paths.items()}
    fingerprints = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in [fixture / "dawn.toml", *paths.values()]}
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "metadata.json").write_text(json.dumps({
        "command": command, "sources": fingerprints, "timing_evidence": False,
    }, indent=2) + "\n")
    boolean_lib = texts["lib"].replace("exported(x: Int) -> Int = x + 1", "exported(x: Int) -> Bool = true")
    boolean_main = texts["main"].replace("probe(x: Int) -> Int", "probe(x: Int) -> Bool")
    steps = [
        ("provider-body", "lib", texts["lib"].replace("x + 1", "x + 9"), None),
        ("provider-signature", "lib", boolean_lib, "main"),
        ("consumer-recovery", "main", boolean_main, None),
        ("provider-error", "lib", boolean_lib.replace("= true", "= missing_value"), "lib"),
        ("provider-recovery", "lib", boolean_lib, None),
        ("provider-move", "lib", "# moved provider\n\n" + boolean_lib, None),
        ("close-provider", "lib", None, "main"),
        ("reopen-provider", "lib", boolean_lib, None),
    ]
    rows, current = [], dict(texts)
    versions = {name: 1 for name in texts}
    client = LspClient(command, ROOT)
    try:
        client.initialize()
        mark = client.mark()
        for name in ("lib", "main"):
            client.send(did_open(uris[name], current[name]))
        initial = client.barrier(mark)
        if any(frame.get("params", {}).get("diagnostics") for frame in initial
               if frame.get("method") == "textDocument/publishDiagnostics"):
            raise RuntimeError("project baseline contains diagnostics")
        for label, name, text, error_owner in steps:
            mark, stderr_mark = client.mark(), len(client.stderr_text())
            versions[name] += 1
            if text is None:
                client.send(did_close(uris[name]))
            elif label == "reopen-provider":
                client.send(did_open(uris[name], text, versions[name]))
                current[name] = text
            else:
                client.send(did_change(uris[name], text, versions[name]))
                current[name] = text
            frames = client.barrier(mark)
            publishes = [frame["params"] for frame in frames
                         if frame.get("method") == "textDocument/publishDiagnostics"]
            latest = {item["uri"]: item for item in publishes}
            if uris["main"] not in latest:
                raise RuntimeError(f"{label}: consumer was not republished")
            if latest[uris["main"]].get("version") != versions["main"]:
                raise RuntimeError(f"{label}: consumer version drift")
            if error_owner:
                if not latest.get(uris[error_owner], {}).get("diagnostics"):
                    raise RuntimeError(f"{label}: missing expected diagnostic")
            elif any(item.get("diagnostics") for item in publishes):
                raise RuntimeError(f"{label}: clean revision retained diagnostics")
            replies = {method: client.result("textDocument/" + method, {
                "textDocument": {"uri": uris["main"]},
                "position": position(current["main"], "exported(x)")})
                for method in ("hover", "definition", "completion")}
            if error_owner is None and (not replies["hover"] or not replies["definition"]):
                raise RuntimeError(f"{label}: clean query target unresolved")
            counts = [decode(line) for line in client.stderr_text()[stderr_mark:].splitlines()
                      if line.startswith("LSP_BODY_STATS\t")]
            if args.expect_reuse and label == "provider-body":
                if len(counts) != 1 or not counts[0]["observed"]:
                    raise RuntimeError("missing project body observation")
                value = counts[0]["counts"]
                if value["checked_bodies"] != 1 or value["reused_bodies"] != 3:
                    raise RuntimeError(f"provider body edit did not check one/reuse three: {value}")
            rows.append({"label": label, "diagnostics": publishes, "replies": replies,
                         "analysis_counts": counts})
            (args.output / "samples.json").write_text(json.dumps(rows, indent=2) + "\n")
        client.shutdown_exit()
    finally:
        (args.output / "stderr.txt").write_text(client.stderr_text())
        client.close()
    semantic = [{key: value for key, value in row.items() if key != "analysis_counts"} for row in rows]
    (args.output / "semantic.json").write_text(json.dumps(semantic, indent=2) + "\n")
    if args.compare:
        if json.loads((args.compare / "metadata.json").read_text())["sources"] != fingerprints:
            raise RuntimeError("project comparison fixture differs")
        if json.loads((args.compare / "semantic.json").read_text()) != semantic:
            raise RuntimeError("project protocol cold/prepared results differ")
    for name, path in paths.items():
        if path.read_text() != texts[name]:
            raise RuntimeError("project benchmark changed a fixture file")
    print(f"OK: {len(rows)} cross-module protocol revisions; no timing claim")


if __name__ == "__main__":
    main()
