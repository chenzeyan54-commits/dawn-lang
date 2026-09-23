#!/usr/bin/env python3
"""Sign a complete gate bundle, attach it to its commit, and ask GitHub to check it.

Why this exists: an external gate run ends with bundle.json on the machine
that ran it. For anyone else to rely on it, it has to be signed by the
maintainer's gate key, stored where a verifier can find it (a note on the
commit, refs/notes/gates), and checked somewhere the maintainer does not
control the verdict (verify-external.yml on a GitHub-hosted runner, which
writes the `gates/maintainer` commit status). Every connection goes out from
this machine; GitHub never connects back.

What it refuses, before anything leaves this machine:
  * a bundle that `bundle.check` finds invalid (schema, leak, a table or
    multiset that is not the commit's), with this machine's own host and user
    names in the leak filter, as when the bundle was built;
  * a bundle that is not complete. Red evidence is not published: there is
    nothing a signed "the gates failed" would let anyone do that the absence
    of a green status does not already say;
  * a bundle whose tree is not the commit named on the command line;
  * an envelope that verify_note.py would not accept against this checkout's
    allowed_signers, so a wrong key is caught here and not on GitHub.

Steps, in order: verify, sign, `git notes --ref=gates add -f`, `git push
<remote> refs/notes/gates`, `gh workflow run verify-external.yml -f sha=<sha>`.

    publish.py <sha> --bundle <file> [--key ~/.ssh/dawn-gates-sign]
               [--repo DIR] [--remote origin]
               [--dry-run]            sign and write the note locally; print
                                      the push and the dispatch, run neither
               [--dry-run-dispatch]   push the note; print the dispatch only
    publish.py --selftest             the refusals and a push to a scratch
                                      bare repository, with a throwaway key

--remote takes anything `git push` does, so a local bare repository stands in
for GitHub in tests. The dispatch always targets the repository `gh` resolves
from the checkout, so it is never run unless the remote is the real one; with
a path as --remote it is refused rather than sent somewhere unrelated.
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bundle as bundle_mod  # noqa: E402
import verify_note  # noqa: E402

DEFAULT_KEY = Path("~/.ssh/dawn-gates-sign")
WORKFLOW = "verify-external.yml"


class Refused(Exception):
    """Nothing was published."""


def git(repo, *args, env=None):
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                            text=True, env=env)
    if result.returncode != 0:
        raise Refused(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def prepare(repo, sha, bundle, key, allowed_signers, identities=None):
    """-> the envelope text, after every refusal above. Writes nothing."""
    if not verify_note.HEX40.match(sha):
        sha = git(repo, "rev-parse", "--verify", f"{sha}^{{commit}}")
    plan, errors, complete, reasons = bundle_mod.check(bundle, repo, identities)
    if errors:
        raise Refused("the bundle is invalid:\n  " + "\n  ".join(errors))
    if not complete or bundle.get("complete") is not True:
        raise Refused("the bundle is not complete; red evidence is not published:\n  "
                      + "\n  ".join(reasons[:20])
                      + (f"\n  (+{len(reasons) - 20} more)" if len(reasons) > 20 else ""))
    if bundle.get("tree") != sha:
        raise Refused(f"the bundle is for {bundle.get('tree')}, not {sha}")
    text = verify_note.envelope_text(bundle, verify_note.sign(bundle, key))
    ok, lines = verify_note.verify_envelope(repo, sha, text, allowed_signers)
    if not ok:
        raise Refused("the signed envelope does not verify here (wrong key?):\n  "
                      + "\n  ".join(ln for ln in lines if ln.startswith("FAIL")))
    return sha, text


def publish(repo, sha, text, remote, push, dispatch, env=None):
    """Write the note, then push and dispatch as asked. Prints each step."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        handle.write(text)
        note_file = handle.name
    try:
        git(repo, "notes", f"--ref={verify_note.NOTES_REF}", "add", "-f", "-F", note_file,
            sha, env=env)
    finally:
        Path(note_file).unlink()
    print(f"note: {verify_note.NOTES_REF} on {sha}")
    push_cmd = ["git", "-C", str(repo), "push", remote, verify_note.NOTES_REF]
    dispatch_cmd = ["gh", "workflow", "run", WORKFLOW, "-f", f"sha={sha}"]
    if not push:
        print("dry run, not pushed: " + " ".join(push_cmd))
    else:
        result = subprocess.run(push_cmd, capture_output=True, text=True, env=env)
        if result.returncode != 0:
            raise Refused(f"push failed (the note is written locally): "
                          f"{result.stderr.strip()}\nIf the remote notes moved, "
                          f"fetch them into a separate ref and merge with "
                          f"`git notes --ref=gates merge` before retrying.")
        print(f"pushed: {verify_note.NOTES_REF} to {remote}")
    if not dispatch:
        print("dry run, not dispatched: " + " ".join(dispatch_cmd))
        return
    result = subprocess.run(dispatch_cmd, cwd=repo, capture_output=True, text=True)
    if result.returncode != 0:
        raise Refused(f"dispatch failed (the note is pushed): {result.stderr.strip()}")
    print(f"dispatched: {WORKFLOW} for {sha}; the status context is gates/maintainer")


# ------------------------------------------------------------------ self-test

def selftest():
    failures = []
    shown = 0
    with tempfile.TemporaryDirectory() as tmp:
        repo, (sha, _sibling, _other) = verify_note.fixture_repo(tmp)
        env = verify_note._isolated_env()
        bare = Path(tmp) / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True, env=env)
        key = verify_note.make_key(tmp, "throwaway-signer")
        stranger = verify_note.make_key(tmp, "throwaway-stranger")
        allowed = verify_note.allowed_signers_for(key, Path(tmp) / "allowed_signers")
        good = verify_note.fixture_bundle(repo, sha)
        red = verify_note.fixture_bundle(repo, sha, exit_code=1)

        def refused(label, bundle, needle, signer=key, target=sha):
            nonlocal shown
            try:
                prepare(repo, target, bundle, signer, allowed, identities=set())
            except Refused as error:
                if needle in str(error):
                    shown += 1
                    print(f"  refused  {label}: "
                          + " / ".join(ln.strip() for ln in str(error).splitlines()[:2]))
                    return
                failures.append(f"{label}: refused for the wrong reason: {error}")
                return
            failures.append(f"not refused: {label}")

        refused("complete=false", red, "not complete")
        lied = dict(red, complete=True)
        refused("complete=false claiming true", lied, "invalid")
        refused("a bundle for another commit", good, "is for", target=_sibling)
        refused("signed with a key not in allowed_signers", good, "does not verify",
                signer=stranger)

        # Green: the same bundle, the right key, pushed to the bare repository.
        # The dispatch is never run here.
        try:
            got_sha, text = prepare(repo, sha, good, key, allowed, identities=set())
            publish(repo, got_sha, text, str(bare), push=True, dispatch=False, env=env)
            clone = Path(tmp) / "clone"
            subprocess.run(["git", "clone", "-q", str(bare), str(clone)], check=True,
                           env=env, capture_output=True)
            subprocess.run(["git", "-C", str(repo), "push", "-q", str(bare), "main"],
                           check=True, env=env, capture_output=True)
            subprocess.run(["git", "-C", str(clone), "fetch", "-q", "origin", "main",
                            f"{verify_note.NOTES_REF}:{verify_note.NOTES_REF}"],
                           check=True, env=env, capture_output=True)
            ok, lines = verify_note.verify_commit(clone, sha, allowed)
            if not ok:
                failures.append("the published note did not verify in a clone: "
                                + " | ".join(lines))
            else:
                shown += 1
                print("  green    complete bundle published to a bare repository "
                      "and verified in a fresh clone")
        except Refused as error:
            failures.append(f"the complete bundle was refused: {error}")

    for line in failures:
        print(f"FAIL publish selftest: {line}", file=sys.stderr)
    if failures:
        return 1
    print(f"OK: publish selftest, {shown} cases")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sha", nargs="?")
    parser.add_argument("--bundle")
    parser.add_argument("--key", default=str(DEFAULT_KEY))
    parser.add_argument("--repo", default=str(HERE.parents[1]))
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--allowed-signers", default=str(verify_note.ALLOWED_SIGNERS))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--dry-run-dispatch", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    if not args.sha or not args.bundle:
        parser.error("a sha and --bundle are required")
    dispatch = not (args.dry_run or args.dry_run_dispatch)
    if dispatch and args.remote != "origin":
        print("publish: refusing to dispatch after pushing somewhere other than origin; "
              "add --dry-run-dispatch", file=sys.stderr)
        return 2
    try:
        bundle = json.loads(Path(args.bundle).read_text())
        sha, text = prepare(args.repo, args.sha, bundle, Path(args.key).expanduser(),
                            args.allowed_signers)
        publish(args.repo, sha, text, args.remote, push=not args.dry_run, dispatch=dispatch)
    except Refused as error:
        print(f"publish: refused: {error}", file=sys.stderr)
        return 1
    except verify_note.EnvelopeError as error:
        print(f"publish: signing failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
