#!/usr/bin/env python3
"""Build a private server using the real immutable analysis-policy entry.

Configuration and optional stderr observation are confined to a copied source
tree. Uninstrumented builds provide the same policy without measurement hooks;
neither variant adds a production command-line flag or protocol method.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

from cold import DAWN, ROOT, edit
from lsp_stats import FIELDS


def configure(text, mode, modules, text_units, products, observe):
    if mode not in {"Legacy", "PreparedBodies", "Cold"} or min(modules, text_units, products) < 0:
        raise ValueError("invalid analysis policy or cache limit")
    text = edit(text, "run_lsp_configured(std_flag, host, legacy_analysis_config())",
                "run_lsp_configured(std_flag, host, LspAnalysisConfig { "
                f"mode: {mode}, max_modules: {modules}, max_text_units: {text_units}, "
                f"max_products: {products}" + " })")
    if not observe:
        return text
    text = edit(text, "      let prog = update.program\n      Workspace {",
                '      benchmark_analysis_stats("project", Some(update.stats))\n'
                "      let prog = update.program\n      Workspace {")
    for anchor in (
        "  let (prog, owner, stats) = standalone_analysis(st, source_path, text, None)",
        "      let (prog, owner, stats) = standalone_analysis(st0, source_path, text, old.standalone_cache)",
    ):
        text = edit(text, anchor, anchor + '\n  benchmark_analysis_stats("standalone", stats)')
    values = ',\n        '.join(f"to_string(stats.{field})" for field in FIELDS)
    text += '''

# Private benchmark observer: absence is not a zero-work assertion.
fn benchmark_analysis_stats(scope: String, value: Option[incremental.Stats]) -> Unit !io =
  match value {
    None -> io.eprintln("LSP_BODY_STATS\\t" ++ scope ++ "\\tunobserved")
    Some(stats) -> io.eprintln("LSP_BODY_STATS\\t" ++ scope ++ "\\t" ++
      join([
        ''' + values + '''
      ], "\\t"))
  }
'''
    return text


def selftest():
    fixture = "\n".join((
        "run_lsp_configured(std_flag, host, legacy_analysis_config())",
        "      let prog = update.program\n      Workspace {",
        "  let (prog, owner, stats) = standalone_analysis(st, source_path, text, None)",
        "      let (prog, owner, stats) = standalone_analysis(st0, source_path, text, old.standalone_cache)",
    ))
    for mode in ("Legacy", "PreparedBodies", "Cold"):
        plain = configure(fixture, mode, 1, 2, 3, False)
        assert f"mode: {mode}, max_modules: 1, max_text_units: 2, max_products: 3" in plain
        assert "benchmark_analysis_stats" not in plain
        observed = configure(fixture, mode, 1, 2, 3, True)
        assert observed.count("benchmark_analysis_stats(") == 4
        for field in FIELDS:
            assert observed.count(f"to_string(stats.{field})") == 1
    for source, mode, modules in (("", "Cold", 1), (fixture + fixture, "Cold", 1),
                                  (fixture, "Invalid", 1), (fixture, "Cold", -1)):
        try:
            configure(source, mode, modules, 2, 3, True)
        except (ValueError, RuntimeError):
            continue
        raise AssertionError("configuration accepted drifted anchors or invalid policy")
    print("OK: three private policies, observation schema, and four rejection controls")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--mode", choices=("Legacy", "PreparedBodies", "Cold"), required=True)
    parser.add_argument("--max-modules", type=int, default=128)
    parser.add_argument("--max-text-units", type=int, default=1048576)
    parser.add_argument("--max-products", type=int, default=10000)
    parser.add_argument("--uninstrumented", action="store_true")
    args = parser.parse_args()
    source = args.source.resolve()
    original = (source / "selfhost/src/lsp/server.dawn").read_text()
    # Validate configuration and exact anchors before creating any output.
    text = configure(original, args.mode, args.max_modules, args.max_text_units,
                     args.max_products, not args.uninstrumented)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    fingerprints = {}
    for directory in ("selfhost", "compiler-plan"):
        shutil.copytree(source / directory, output / directory,
                        ignore=shutil.ignore_patterns("build", ".dawn"))
        for path in sorted((source / directory).rglob("*")):
            if path.is_file() and path.suffix in (".dawn", ".toml", ".lock"):
                fingerprints[str(path.relative_to(source))] = hashlib.sha256(path.read_bytes()).hexdigest()
    (output / "packages").symlink_to(source / "packages", target_is_directory=True)
    (output / "selfhost/src/lsp/server.dawn").write_text(text)
    with (output / "build.log").open("w") as log:
        subprocess.run([DAWN, "build", str(output / "selfhost"), "-o", str(output / "compiler.jar"),
                        "--vendor", "org/objectweb/asm", "--vendor", "coursierapi"],
                       cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600)
    (output / "metadata.json").write_text(json.dumps({
        "source": str(source), "sources": fingerprints, "mode": args.mode,
        "max_modules": args.max_modules, "max_text_units": args.max_text_units,
        "max_products": args.max_products, "instrumented": not args.uninstrumented,
        "stats_fields": FIELDS,
        "note": "cold standalone work is unobserved, not zero; timing includes optional stderr observation",
        "configured_server_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "compiler_sha256": hashlib.sha256((output / "compiler.jar").read_bytes()).hexdigest(),
    }, indent=2) + "\n")


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        selftest()
    else:
        main()
