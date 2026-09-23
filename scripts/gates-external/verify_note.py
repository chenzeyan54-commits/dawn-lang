#!/usr/bin/env python3
"""Verify the signed gate evidence attached to one commit as a git note.

Why this exists: bundle.json says what an external gate run executed and what
it returned, and `bundle.py verify` recomputes everything about it from git.
What it cannot say is who produced it. A bundle anyone can write is a claim,
not evidence. So the maintainer signs the bundle with one dedicated key
(publish.py), stores bundle and signature together as a note on the commit
(refs/notes/gates), and this script checks both halves: the signature against
the one key in allowed_signers, and the bundle against the commit.

The note is an envelope, a JSON object with exactly two fields:

    {"bundle": <bundle.json as an object>, "signature": "<armored SSH signature>"}

What is signed is not the envelope's text but the bundle's canonical bytes,
`json.dumps(bundle, sort_keys=True, separators=(",", ":"))` plus a newline,
so the envelope can be pretty-printed and re-serialised freely while the
signature stays about the content. The namespace and the signer identity are
both `dawn-gates`; a signature made for any other namespace does not verify.

The checklist, every item of which must hold (all are evaluated and printed,
so a failure names everything that is wrong, not only the first thing):

  1. note      the commit carries a note on refs/notes/gates
  2. envelope  the note is a JSON object with exactly `bundle` and `signature`,
               and no key appears twice anywhere in it
  3. signature `ssh-keygen -Y verify -n dawn-gates -I dawn-gates` against
               allowed_signers accepts the signature over the canonical bytes
  4. tree      bundle.tree is the commit under test
  5. blob      bundle.gates_blob is `git rev-parse <sha>:.github/workflows/gates.yml`
  6. bundle    bundle.check (the code `bundle.py verify` runs, not a copy):
               schema whitelist and leak shapes, the substitution table is
               exactly the one this commit's gates.yml produces (so every row
               is a known one), the (job, run text) multiset of executed steps
               equals the commit's, every executed step exited 0, and the
               bundle's own `complete` agrees with the recomputation
  7. complete  the bundle says complete=true, and so does the recomputation

allowed_signers is read from this script's own checkout, never from the commit
under test. On GitHub that checkout is the default branch (see
verify-external.yml): a commit that edits allowed_signers must not be able to
vouch for itself.

Modes:
  verify_note.py --sha SHA [--repo DIR] [--allowed-signers FILE]
                   exit 0 when every item holds, 1 otherwise
  verify_note.py --selftest
                   a scratch repository and two throwaway keys; each negative
                   control is shown red, then green with the mutation undone
"""

import argparse
import copy
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bundle as bundle_mod  # noqa: E402
import gatesplan  # noqa: E402

NAMESPACE = "dawn-gates"
IDENTITY = "dawn-gates"
NOTES_REF = "refs/notes/gates"
ALLOWED_SIGNERS = HERE / "allowed_signers"
ENVELOPE_FIELDS = {"bundle", "signature"}
SIG_HEADER = "-----BEGIN SSH SIGNATURE-----"
HEX40 = re.compile(r"^[0-9a-f]{40}$")


class EnvelopeError(Exception):
    """The note is not an envelope this script can read."""


def canonical_bytes(bundle):
    """The exact bytes that are signed and verified."""
    return (json.dumps(bundle, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sign(bundle, key):
    """An armored SSH signature over the bundle's canonical bytes."""
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "bundle"
        data.write_bytes(canonical_bytes(bundle))
        result = subprocess.run(
            ["ssh-keygen", "-q", "-Y", "sign", "-n", NAMESPACE, "-f", str(key), str(data)],
            capture_output=True, text=True)
        if result.returncode != 0:
            raise EnvelopeError(f"ssh-keygen -Y sign failed: {result.stderr.strip()}")
        return (Path(tmp) / "bundle.sig").read_text()


def envelope_text(bundle, signature):
    """The note body. Pretty-printed; the signature does not depend on it."""
    return json.dumps({"bundle": bundle, "signature": signature},
                      indent=2, sort_keys=True) + "\n"


def _no_duplicate_keys(pairs):
    keys = [k for k, _ in pairs]
    duplicated = sorted({k for k in keys if keys.count(k) > 1})
    if duplicated:
        raise EnvelopeError(f"duplicate keys {duplicated}")
    return dict(pairs)


def parse_envelope(text):
    """-> (bundle, signature), or EnvelopeError."""
    try:
        envelope = json.loads(text, object_pairs_hook=_no_duplicate_keys)
    except ValueError as error:
        raise EnvelopeError(f"not JSON: {error}")
    if not isinstance(envelope, dict):
        raise EnvelopeError("not a JSON object")
    if set(envelope) != ENVELOPE_FIELDS:
        raise EnvelopeError(f"fields are {sorted(envelope)}, want {sorted(ENVELOPE_FIELDS)}")
    signature = envelope["signature"]
    if not isinstance(signature, str) or not signature.startswith(SIG_HEADER):
        raise EnvelopeError("signature is not an armored SSH signature")
    if not isinstance(envelope["bundle"], dict):
        raise EnvelopeError("bundle is not an object")
    return envelope["bundle"], signature


def verify_signature(bundle, signature, allowed_signers):
    """None when the signature holds, else ssh-keygen's reason."""
    with tempfile.TemporaryDirectory() as tmp:
        sig = Path(tmp) / "bundle.sig"
        sig.write_text(signature)
        result = subprocess.run(
            ["ssh-keygen", "-Y", "verify", "-n", NAMESPACE, "-I", IDENTITY,
             "-f", str(allowed_signers), "-s", str(sig)],
            input=canonical_bytes(bundle), capture_output=True)
    if result.returncode == 0:
        return None
    text = (result.stderr or result.stdout).decode(errors="replace").strip()
    return text.splitlines()[-1] if text else f"ssh-keygen exit {result.returncode}"


def git(repo, *args, check=True):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, check=check)


def read_note(repo, sha):
    """The note text on sha, or None."""
    result = git(repo, "notes", f"--ref={NOTES_REF}", "show", sha, check=False)
    return result.stdout if result.returncode == 0 else None


def verify_envelope(repo, sha, text, allowed_signers=ALLOWED_SIGNERS):
    """-> (ok, lines). Every checklist item is evaluated and reported."""
    lines = []
    ok = True

    def item(name, failure, detail=""):
        nonlocal ok
        if failure:
            ok = False
            lines.append(f"FAIL {name:9} {failure}")
        else:
            lines.append(f"ok   {name:9} {detail}".rstrip())

    if not HEX40.match(sha or ""):
        item("sha", f"{sha!r} is not a 40-hex commit name")
        return False, lines
    resolved = git(repo, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}", check=False)
    if resolved.returncode != 0 or resolved.stdout.strip() != sha:
        item("sha", f"{sha} is not a commit in this repository")
        return False, lines
    if text is None:
        item("note", f"no note on {NOTES_REF} for {sha}")
        return False, lines
    item("note", None, NOTES_REF)
    try:
        bundle, signature = parse_envelope(text)
    except EnvelopeError as error:
        item("envelope", str(error))
        return False, lines
    item("envelope", None, "bundle + signature")

    failure = verify_signature(bundle, signature, allowed_signers)
    item("signature", failure and f"does not verify: {failure}",
         f"namespace {NAMESPACE}, identity {IDENTITY}")

    tree = bundle.get("tree")
    item("tree", tree != sha and f"bundle.tree is {tree!r}, the commit is {sha}", sha)

    blob = git(repo, "rev-parse", f"{sha}:{gatesplan.GATES_PATH}", check=False)
    blob = blob.stdout.strip() if blob.returncode == 0 else None
    item("blob", (blob is None and f"{gatesplan.GATES_PATH} is not in {sha}")
         or (bundle.get("gates_blob") != blob
             and f"bundle.gates_blob is {bundle.get('gates_blob')!r}, "
                 f"{gatesplan.GATES_PATH} at {sha} is {blob}"),
         blob or "")

    plan, errors, complete, reasons = bundle_mod.check(bundle, repo, identities=set())
    item("bundle", errors and "; ".join(errors),
         "" if plan is None else f"{len(gatesplan.run_commands(plan['jobs']))} run steps, "
                  f"{len(bundle.get('substitutions') or [])} substitution rows")

    claimed = bundle.get("complete")
    item("complete",
         (claimed is not True and f"bundle says complete={json.dumps(claimed)}")
         or (not complete and "recomputed complete=false: " + "; ".join(reasons[:5])
             + (f" (+{len(reasons) - 5} more)" if len(reasons) > 5 else "")),
         "claimed and recomputed")
    return ok, lines


def verify_commit(repo, sha, allowed_signers=ALLOWED_SIGNERS):
    return verify_envelope(repo, sha, read_note(repo, sha) if HEX40.match(sha or "") else None,
                           allowed_signers)


# ------------------------------------------------------------------ self-test

def _isolated_env():
    """Git with no user or system configuration, so hooks, signing settings
    and templates of the machine running the self-test cannot leak in."""
    env = dict(os.environ)
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
               GIT_AUTHOR_NAME="selftest", GIT_AUTHOR_EMAIL="selftest",
               GIT_COMMITTER_NAME="selftest", GIT_COMMITTER_EMAIL="selftest")
    return env


def fixture_repo(root):
    """A scratch repository with two commits carrying bundle.py's fixture
    gates.yml, and a third whose gates.yml differs. -> (repo, [sha1, sha2, sha3])."""
    repo = Path(root) / "repo"
    env = _isolated_env()

    def run(*args):
        return subprocess.run(["git", "-C", str(repo), *args], check=True,
                              capture_output=True, text=True, env=env).stdout.strip()

    repo.mkdir()
    run("init", "-q", "-b", "main")
    gates = repo / ".github" / "workflows" / "gates.yml"
    gates.parent.mkdir(parents=True)
    gates.write_text(bundle_mod.FIXTURE_GATES_YML)
    run("add", ".")
    run("commit", "-q", "-m", "one")
    first = run("rev-parse", "HEAD")
    (repo / "README").write_text("two\n")
    run("add", ".")
    run("commit", "-q", "-m", "two")
    second = run("rev-parse", "HEAD")
    gates.write_text(bundle_mod.FIXTURE_GATES_YML.replace("--more", "--most"))
    run("commit", "-q", "-am", "three")
    third = run("rev-parse", "HEAD")
    return repo, [first, second, third]


def fixture_bundle(repo, sha, exit_code=0):
    """A bundle bundle.build accepts for sha: every run step executed."""
    plan = gatesplan.plan_at(repo, sha)
    steps = [{"job": job, "name": name, "command": command, "exit_code": exit_code,
              "stdout_sha256": "a" * 64, "stderr_sha256": "b" * 64, "executed": True}
             for job, name, command in (
                 (j["id"], a["name"], a["command"])
                 for j in plan["jobs"] for a in j["actions"] if a["kind"] == "run")]
    return bundle_mod.build(plan, steps, dict(bundle_mod.TOOLCHAIN_OK), identities=set())


def make_key(directory, name):
    key = Path(directory) / name
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name,
                    "-f", str(key)], check=True)
    return key


def allowed_signers_for(key, path):
    kind, blob = Path(f"{key}.pub").read_text().split()[:2]
    Path(path).write_text(f'{IDENTITY} namespaces="{NAMESPACE}" {kind} {blob}\n')
    return path


def selftest():
    failures = []
    shown = 0
    with tempfile.TemporaryDirectory() as tmp:
        repo, (sha, sibling, other) = fixture_repo(tmp)
        good_key = make_key(tmp, "throwaway-signer")
        stranger = make_key(tmp, "throwaway-stranger")
        allowed = allowed_signers_for(good_key, Path(tmp) / "allowed_signers")
        clean = fixture_bundle(repo, sha)
        clean_note = envelope_text(clean, sign(clean, good_key))

        def outcome(text):
            return verify_envelope(repo, sha, text, allowed)

        ok, lines = outcome(clean_note)
        if not ok:
            failures.append("the clean envelope did not verify: " + " | ".join(lines))
        else:
            print("  green    clean envelope: " + ", ".join(
                line.split()[1] for line in lines))

        def signed(bundle, key=good_key):
            return envelope_text(bundle, sign(bundle, key))

        def with_bundle(mutate):
            mutated = copy.deepcopy(clean)
            mutate(mutated)
            return signed(mutated)

        def flip_one_byte(text):
            # One hex digit of one output digest, after signing: the JSON stays
            # valid and every structural check still passes, so only the
            # signature can notice.
            at = text.index('"stdout_sha256": "') + len('"stdout_sha256": "')
            return text[:at] + ("b" if text[at] == "a" else "a") + text[at + 1:]

        other_blob = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", f"{other}:{gatesplan.GATES_PATH}"],
            capture_output=True, text=True, check=True).stdout.strip()

        cases = [
            ("one byte of the bundle changed after signing", "signature",
             lambda: flip_one_byte(clean_note)),
            ("signed by a key not in allowed_signers", "signature",
             lambda: signed(clean, stranger)),
            ("bundle.tree is another commit with the same gates.yml", "tree",
             lambda: with_bundle(lambda b: b.__setitem__("tree", sibling))),
            ("bundle.gates_blob is another gates.yml", "blob",
             lambda: with_bundle(lambda b: b.__setitem__("gates_blob", other_blob))),
            ("complete=false (one step exited 1)", "complete",
             lambda: signed(fixture_bundle(repo, sha, exit_code=1))),
            ("an unknown substitution row", "bundle",
             lambda: with_bundle(lambda b: b["substitutions"].append(
                 {"subject": "actions/checkout@v5", "replacement": "tree-worktree"}))),
            ("a known replacement id on a row it does not belong to", "bundle",
             lambda: with_bundle(lambda b: b["substitutions"][0].__setitem__(
                 "replacement", "noop"))),
            ("an extra field in the envelope", "envelope",
             lambda: clean_note.replace('"bundle":', '"note": "x",\n  "bundle":', 1)),
            ("a duplicated key in the envelope", "envelope",
             lambda: clean_note.replace('"bundle":', '"signature": "x",\n  "bundle":', 1)),
            ("no note at all", "note", lambda: None),
        ]
        for label, needle, make in cases:
            ok, lines = outcome(make())
            hit = next((line for line in lines if line.startswith(f"FAIL {needle}")), None)
            if ok or hit is None:
                failures.append(f"not red: {label} ({' | '.join(lines)})")
                continue
            ok_again, lines_again = outcome(clean_note)
            if not ok_again:
                failures.append(f"not green again after undoing: {label}")
                continue
            shown += 1
            print(f"  red      {label}: {hit}")
            print(f"  green    {label}, undone: {len(lines_again)} items ok")

    for line in failures:
        print(f"FAIL verify_note selftest: {line}", file=sys.stderr)
    if failures:
        return 1
    print(f"OK: verify_note selftest, {shown} negative controls red then green")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--sha")
    parser.add_argument("--repo", default=str(HERE.parents[1]))
    parser.add_argument("--allowed-signers", default=str(ALLOWED_SIGNERS))
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    if not args.sha:
        parser.error("--sha is required")
    ok, lines = verify_commit(args.repo, args.sha, args.allowed_signers)
    for line in lines:
        print(line)
    print(f"{'VERIFIED' if ok else 'NOT VERIFIED'}: {args.sha}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
