#!/usr/bin/env python3
"""The offline input pack: every byte a gate run needs from the network.

Why this exists. A cluster container with no route to the internet cannot run
setup-graalvm, download node, fetch the seed or ask Maven Central for asm, and
a workstation that can should not be trusted to fetch the same bytes twice.
So the downloads happen once, on a machine with a network, into a prefix
(prefix.py describes its layout); each one is checked against a digest that
lives in the repository, inputs.lock.json, and never against a checksum served
next to the file. The prefix then travels as a directory, and `verify` re-hashes
everything wherever it lands.

What the lock pins and what it does not:
  - downloads (GraalVM, node, wasi-sdk, python): URL and sha256, in the lock;
  - the bootstrap seed jar and its std: checked against the repository's own
    scripts/seed-checksums.txt and seed-std-checksums.txt, the tables seedjar.sh
    reads. A copy in the lock would be a second table to advance at every
    release, and advance-seed.sh does not know about it;
  - the coursier cache: produced by running `./bin/dawn --version` once with
    the cache pointed into the prefix; its jars are checked against
    selfhost/dawn.lock, and the whole tree's digest goes into MANIFEST.json.

One change is made to an unpacked toolchain, and it is part of the layout,
not of the download: GraalVM's bin/java becomes a shim that execs the real
launcher, renamed bin/java.real, with -XX:-UsePerfData in front of the
caller's arguments. HotSpot writes /tmp/hsperfdata_<user>/<pid> for every
JVM, in a /tmp it hardcodes whatever TMPDIR says, so every gate step that
starts a JVM wrote outside the prefix. The only switch that turns it off is
that flag, and the environment variables that could carry it
(JAVA_TOOL_OPTIONS, JDK_JAVA_OPTIONS) make each JVM print "Picked up ..." on
stderr, which changes the output under test. The lock entry is untouched:
the archive is the same bytes, the shim is written after unpacking (build,
install), and MANIFEST records the digest of bin/java as the archive has it,
which verify holds bin/java.real to, with the shim's exact bytes.

MANIFEST.json (in the prefix, not the repository) records what build put
there: per item the prefix-relative path, bytes, and a file sha256 or a tree
digest. `verify` recomputes every one of them and, for downloads, also checks
the lock, so an edited MANIFEST cannot vouch for an edited archive.

Subcommands:
    build   --prefix P [--repo R] [--seed-cache DIR]
    install --prefix P      extract toolchains from inputs/downloads (a shipped
                            pack) and verify
    verify  --prefix P [--repo R]
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import prefix as prefix_mod  # noqa: E402


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_digest(root):
    """A digest of a directory's names, contents, executable bits and links.

    Modes other than the executable bit and owners are left out: tar applies
    the umask for an ordinary user and keeps the archive's modes for root, and
    the same pack must verify on both. `__pycache__` directories are left out
    as well: python-build-standalone ships no bytecode, the interpreter writes
    it beside the stdlib on first import, and it validates each file against
    its source itself. Returns (digest, bytes, files).
    """
    root = Path(root)
    digest = hashlib.sha256()
    total = files = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        rel_dir = Path(dirpath).relative_to(root)
        for name in sorted(filenames + [d for d in dirnames if os.path.islink(os.path.join(dirpath, d))]):
            path = Path(dirpath) / name
            rel = (rel_dir / name).as_posix()
            if path.is_symlink():
                digest.update(f"L {rel} {os.readlink(path)}\n".encode())
            elif path.is_file():
                size = path.stat().st_size
                x = "x" if path.stat().st_mode & 0o100 else "-"
                digest.update(f"F {rel} {x} {size} {sha256_file(path)}\n".encode())
                total += size
                files += 1
        # a symlinked directory was recorded as a link; do not walk into it
        dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]
    return digest.hexdigest(), total, files


def seed_tree_sha(root):
    """seedjar.sh's seed_tree_sha: files sorted by `./path`, each contributing
    `<bytes> ./<path>\\n` then its content, the whole through SHA-256."""
    root = Path(root)
    names = sorted(("./" + p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()),
                   key=lambda s: s.encode())
    digest = hashlib.sha256()
    for name in names:
        data = (root / name[2:]).read_bytes()
        digest.update(f"{len(data)} {name}\n".encode())
        digest.update(data)
    return digest.hexdigest()


def table_value(path, tag):
    for line in Path(path).read_text().splitlines():
        fields = line.split()
        if len(fields) == 2 and not line.startswith("#") and fields[1] == tag:
            return fields[0]
    return None


def archive_name(item):
    return urllib.parse.unquote(item["url"].rsplit("/", 1)[1])


def git_common_dir(repo):
    return Path(subprocess.run(["git", "-C", str(repo), "rev-parse", "--path-format=absolute",
                                "--git-common-dir"], check=True, capture_output=True,
                               text=True).stdout.strip())


# The shim is the same bytes in every prefix: it finds java.real beside
# itself, so no path is written into it and the toolchain's tree digest does
# not depend on where the prefix lives.
JAVA_SHIM = """#!/bin/sh
# Written by scripts/gates-external/inputs.py, not part of GraalVM: every JVM
# of a gate run starts without hsperfdata, which HotSpot would write to /tmp.
exec "$(dirname -- "$(readlink -f -- "$0")")/java.real" -XX:-UsePerfData "$@"
"""


def java_member(archive):
    """bin/java's path inside the GraalVM archive (one top-level directory)."""
    listing = subprocess.run(["tar", "tzf", str(archive)], check=True, capture_output=True,
                             text=True).stdout.splitlines()
    top = listing[0].split("/", 1)[0]
    return f"{top}/bin/java"


def archive_java_sha256(archive):
    """sha256 of bin/java as the archive holds it, read without unpacking."""
    done = subprocess.run(["tar", "xzf", str(archive), "-O", java_member(archive)],
                          check=True, capture_output=True)
    return hashlib.sha256(done.stdout).hexdigest()


def install_java_shim(target):
    """bin/java -> bin/java.real, and the shim at bin/java; idempotent."""
    java = Path(target) / "bin" / "java"
    real = java.with_name("java.real")
    if not real.exists():
        java.rename(real)
    if not java.exists() or java.read_text(errors="replace") != JAVA_SHIM:
        tmp = java.with_name("java.shim.tmp")
        tmp.write_text(JAVA_SHIM)
        tmp.chmod(0o755)
        tmp.rename(java)


def check_java_shim(target, want_sha):
    """What verify holds a GraalVM toolchain to; a list of problems."""
    java = Path(target) / "bin" / "java"
    real = java.with_name("java.real")
    problems = []
    if not want_sha:
        problems.append("MANIFEST records no bin/java digest (a prefix from before the shim; "
                        "run inputs.py build or install)")
    if not java.is_file() or java.read_text(errors="replace") != JAVA_SHIM:
        problems.append("bin/java is not the -XX:-UsePerfData shim")
    if not real.is_file():
        problems.append("bin/java.real is missing")
    elif want_sha and sha256_file(real) != want_sha:
        problems.append("bin/java.real is not the archive's bin/java")
    return problems


def mib(n):
    return f"{n / 2**20:.1f} MiB"


# ------------------------------------------------------------- downloads

def fetch(item, prefix, log):
    dst = prefix / "inputs" / "downloads" / archive_name(item)
    if dst.exists() and sha256_file(dst) == item["sha256"]:
        log(f"{item['name']}: {dst.name} already present and matches the lock")
        return dst, 0.0
    part = dst.with_name(dst.name + ".part")
    t0 = time.monotonic()
    subprocess.run(["curl", "-fL", "--retry", "3", "-sS", "-o", str(part), item["url"]], check=True)
    seconds = time.monotonic() - t0
    got = sha256_file(part)
    if got != item["sha256"]:
        part.unlink()
        raise SystemExit(f"inputs: {item['name']} {item['url']} has sha256 {got}, "
                         f"the lock says {item['sha256']}; refusing it")
    part.rename(dst)
    log(f"{item['name']}: downloaded {mib(dst.stat().st_size)} in {seconds:.1f}s")
    return dst, seconds


def extract(item, prefix, archive, log):
    target = prefix / "toolchain" / item["dir"]
    tmp = target.with_name(target.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    t0 = time.monotonic()
    subprocess.run(["tar", "xf", str(archive), "-C", str(tmp), "--strip-components=1"],
                   check=True)
    if item["name"] == "graalvm":
        install_java_shim(tmp)
    shutil.rmtree(target, ignore_errors=True)
    tmp.rename(target)
    log(f"{item['name']}: extracted into toolchain/{item['dir']} in {time.monotonic() - t0:.1f}s")
    return target


# -------------------------------------------------------------- commands

def build(args):
    prefix = Path(args.prefix).resolve()
    repo = Path(args.repo).resolve()
    prefix_mod.ensure_layout(prefix)
    log = lambda m: print(f"inputs: {m}", flush=True)  # noqa: E731
    items = []
    for item in prefix_mod.load_lock()["downloads"]:
        archive, seconds = fetch(item, prefix, log)
        target = prefix / "toolchain" / item["dir"]
        if not target.exists():
            extract(item, prefix, archive, log)
        extra = {}
        if item["name"] == "graalvm":
            install_java_shim(target)
            extra["java_sha256"] = archive_java_sha256(archive)
        tree, size, files = tree_digest(target)
        items.append({"name": item["name"], "version": item["version"], "kind": "download",
                      "path": archive.relative_to(prefix).as_posix(),
                      "bytes": archive.stat().st_size, "sha256": item["sha256"],
                      "source": item["url"], "download_seconds": round(seconds, 1)})
        items.append({"name": item["name"], "version": item["version"], "kind": "toolchain",
                      "path": target.relative_to(prefix).as_posix(), "bytes": size,
                      "files": files, "tree_sha256": tree,
                      "source": f"extracted from {archive.name}", **extra})

    tag = (repo / "scripts/seed-release.txt").read_text().strip()
    seed_cache = Path(args.seed_cache) if args.seed_cache else git_common_dir(repo).parent / ".dawn/seeds"
    jar_want = table_value(repo / "scripts/seed-checksums.txt", tag)
    std_want = table_value(repo / "scripts/seed-std-checksums.txt", tag)
    seed_dir = prefix / "inputs" / "seeds" / tag
    seed_dir.mkdir(parents=True, exist_ok=True)
    jar = seed_dir / "seed.jar"
    if not jar.exists() or sha256_file(jar) != jar_want:
        shutil.copy2(seed_cache / tag / "seed.jar", jar)
    if sha256_file(jar) != jar_want:
        raise SystemExit(f"inputs: the {tag} seed jar does not match scripts/seed-checksums.txt")
    std_dir = prefix / "inputs" / "std-seeds" / tag
    if not std_dir.exists() or seed_tree_sha(std_dir) != std_want:
        shutil.rmtree(std_dir, ignore_errors=True)
        shutil.copytree(seed_cache / f"std-{tag}", std_dir)
    if seed_tree_sha(std_dir) != std_want:
        raise SystemExit(f"inputs: the {tag} std does not match scripts/seed-std-checksums.txt")
    items.append({"name": "seed", "version": tag, "kind": "seed",
                  "path": jar.relative_to(prefix).as_posix(), "bytes": jar.stat().st_size,
                  "sha256": jar_want, "source": "scripts/seed-checksums.txt"})
    items.append({"name": "std-seed", "version": tag, "kind": "std-seed",
                  "path": std_dir.relative_to(prefix).as_posix(),
                  "bytes": sum(p.stat().st_size for p in std_dir.rglob("*") if p.is_file()),
                  "seed_tree_sha256": std_want, "tree_sha256": tree_digest(std_dir)[0],
                  "source": "scripts/seed-std-checksums.txt"})
    log(f"seed {tag}: jar and std match the repository's tables")

    coursier = prefix / "inputs" / "coursier"
    if args.refresh_coursier or not any(coursier.rglob("*.jar")):
        prime_coursier(prefix, repo, tag, coursier, log)
    check_coursier_against_lock(repo, coursier)
    tree, size, files = tree_digest(coursier)
    items.append({"name": "coursier", "version": "selfhost/dawn.lock", "kind": "coursier",
                  "path": "inputs/coursier", "bytes": size, "files": files, "tree_sha256": tree,
                  "source": "./bin/dawn --version with COURSIER_CACHE in the prefix"})

    manifest = {"schema": 1, "items": items}
    (prefix / "inputs" / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")
    shutil.copy2(prefix_mod.LOCK_FILE, prefix / "inputs" / "inputs.lock.json")
    for row in items:
        print(f"  {row['kind']:10} {row['name']:9} {row['version']:20} {mib(row['bytes']):>11}  {row['path']}")
    return verify(argparse.Namespace(prefix=str(prefix), repo=str(repo)))


def prime_coursier(prefix, repo, tag, coursier, log):
    """Run `./bin/dawn --version` once, in the prefix, with the cache there."""
    work = prefix / "tmp" / "inputs-build"
    shutil.rmtree(work, ignore_errors=True)
    (work / "tmp").mkdir(parents=True)
    tree = work / "tree"
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True,
                          capture_output=True, text=True).stdout.strip()
    env = prefix_mod.job_env(prefix, tmpdir=work / "tmp", runner_temp=work / "tmp")
    env["COURSIER_CACHE"] = str(coursier)
    subprocess.run(["git", "clone", "-q", "--shared", "--no-checkout", str(repo), str(tree)],
                   check=True, env=env)
    subprocess.run(["git", "-C", str(tree), "checkout", "-q", "--detach", head], check=True, env=env)
    (tree / ".dawn/seeds").mkdir(parents=True)
    shutil.copytree(prefix / "inputs/seeds" / tag, tree / ".dawn/seeds" / tag)
    shutil.copytree(prefix / "inputs/std-seeds" / tag, tree / ".dawn/seeds" / f"std-{tag}")
    shutil.rmtree(coursier, ignore_errors=True)
    coursier.mkdir(parents=True)
    t0 = time.monotonic()
    done = subprocess.run(["./bin/dawn", "--version"], cwd=tree, env=env,
                          capture_output=True, text=True)
    if done.returncode != 0:
        raise SystemExit(f"inputs: ./bin/dawn --version failed while priming coursier:\n"
                         f"{done.stdout}{done.stderr}")
    log(f"coursier: primed by ./bin/dawn --version ({done.stdout.strip()}) in "
        f"{time.monotonic() - t0:.0f}s")
    shutil.rmtree(work, ignore_errors=True)


def check_coursier_against_lock(repo, coursier):
    want = {}
    for line in (repo / "selfhost/dawn.lock").read_text().splitlines():
        fields = line.split()
        if len(fields) == 3 and fields[0] == "artifact":
            want[fields[2]] = fields[1]
    have = {}
    for jar in coursier.rglob("*.jar"):
        have.setdefault(jar.name, set()).add(sha256_file(jar))
    for name, digest in want.items():
        if digest not in have.get(name, set()):
            raise SystemExit(f"inputs: the coursier cache has no {name} with the sha256 "
                             f"selfhost/dawn.lock records")


def install(args):
    """Extract every toolchain from a shipped inputs/downloads, then verify."""
    prefix = Path(args.prefix).resolve()
    prefix_mod.ensure_layout(prefix)
    log = lambda m: print(f"inputs: {m}", flush=True)  # noqa: E731
    manifest = json.loads((prefix / "inputs" / "MANIFEST.json").read_text())
    trees = {row["name"]: row["tree_sha256"] for row in manifest["items"] if row["kind"] == "toolchain"}
    for item in prefix_mod.load_lock()["downloads"]:
        archive = prefix / "inputs" / "downloads" / archive_name(item)
        if sha256_file(archive) != item["sha256"]:
            raise SystemExit(f"inputs: {archive.name} does not match the lock")
        target = prefix / "toolchain" / item["dir"]
        if target.exists() and tree_digest(target)[0] == trees.get(item["name"]):
            log(f"{item['name']}: toolchain/{item['dir']} already matches")
            continue
        extract(item, prefix, archive, log)
    return verify(argparse.Namespace(prefix=str(prefix), repo=None))


def verify(args):
    prefix = Path(args.prefix).resolve()
    manifest_path = prefix / "inputs" / "MANIFEST.json"
    if not manifest_path.exists():
        print(f"inputs verify: no {manifest_path}")
        return 1
    manifest = json.loads(manifest_path.read_text())
    lock = {item["name"]: item for item in prefix_mod.load_lock()["downloads"]}
    bad = 0
    t0 = time.monotonic()
    for row in manifest["items"]:
        path = prefix / row["path"]
        problems = []
        if not path.exists():
            problems.append("missing")
        elif row["kind"] == "download":
            got = sha256_file(path)
            if got != lock[row["name"]]["sha256"]:
                problems.append(f"sha256 {got} is not the lock's {lock[row['name']]['sha256']}")
            if got != row["sha256"]:
                problems.append("sha256 differs from MANIFEST")
        elif row["kind"] == "seed":
            got = sha256_file(path)
            if got != row["sha256"]:
                problems.append(f"sha256 {got} differs from MANIFEST")
            if args.repo:
                want = table_value(Path(args.repo) / "scripts/seed-checksums.txt", row["version"])
                if got != want:
                    problems.append("not the digest scripts/seed-checksums.txt records")
        else:
            got = tree_digest(path)[0]
            if got != row["tree_sha256"]:
                problems.append(f"tree digest {got} differs from MANIFEST")
            if row["kind"] == "toolchain" and row["name"] == "graalvm":
                problems += check_java_shim(path, row.get("java_sha256"))
            if row["kind"] == "std-seed":
                seed_sha = seed_tree_sha(path)
                if seed_sha != row["seed_tree_sha256"]:
                    problems.append("seed_tree_sha differs from MANIFEST")
                if args.repo and seed_sha != table_value(
                        Path(args.repo) / "scripts/seed-std-checksums.txt", row["version"]):
                    problems.append("not the digest scripts/seed-std-checksums.txt records")
        if row["kind"] == "download" and row["name"] not in lock:
            problems.append("not in inputs.lock.json")
        status = "ok  " if not problems else "FAIL"
        print(f"{status} {row['kind']:10} {row['name']:9} {row['version']:20} "
              f"{mib(row['bytes']):>11}  {row['path']}{'  ' + '; '.join(problems) if problems else ''}")
        bad += bool(problems)
    missing = set(lock) - {r["name"] for r in manifest["items"] if r["kind"] == "download"}
    for name in sorted(missing):
        print(f"FAIL download   {name:9} in inputs.lock.json but not in MANIFEST")
        bad += 1
    print(f"inputs verify: {'green' if not bad else f'RED, {bad} item(s)'} "
          f"({time.monotonic() - t0:.1f}s)")
    return 0 if not bad else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("build")
    p.add_argument("--prefix", required=True)
    p.add_argument("--repo", default=str(HERE.parents[1]))
    p.add_argument("--seed-cache")
    p.add_argument("--refresh-coursier", action="store_true")
    p = sub.add_parser("install")
    p.add_argument("--prefix", required=True)
    p = sub.add_parser("verify")
    p.add_argument("--prefix", required=True)
    p.add_argument("--repo")
    args = parser.parse_args()
    return {"build": build, "install": install, "verify": verify}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
