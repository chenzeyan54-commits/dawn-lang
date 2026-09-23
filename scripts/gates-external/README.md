# gates-external

Run every gate job of `.github/workflows/gates.yml`, as that file stands at
one commit, somewhere other than GitHub, and leave a bundle that says what ran
and what it returned; then sign that bundle and have GitHub check it. The
running is local tooling. The one workflow that reads this directory is
`verify-external.yml`, which runs `verify_note.py` when dispatched.

The design and its reasons are in
[docs/gates-external-design.md](../../docs/gates-external-design.md) (Chinese).

```bash
scripts/gates-external/run.sh --sha <sha> --backend local --jobs 8 --out <dir> [--keep-going]
scripts/gates-external/bundle.py verify <dir>/bundle.json   # recompute everything from git
scripts/gates-external/bundle.py --selftest
scripts/gates-external/gatesplan.py --self-test
scripts/gates-external/run.sh --sha <sha> --backend local --out <dir> --dry-run   # the plan only

scripts/gates-external/publish.py <sha> --bundle <dir>/bundle.json   # sign, note, push, dispatch
scripts/gates-external/publish.py <sha> --bundle <dir>/bundle.json --remote <bare> --dry-run-dispatch
scripts/gates-external/verify_note.py --sha <sha>    # what verify-external.yml runs
scripts/gates-external/verify_note.py --selftest
scripts/gates-external/publish.py --selftest
```

Exit status of `run.sh`: 0 complete, 1 ran but not complete, 2 refused to
plan, 3 the bundle was refused (leak or schema), nothing written.

## Files

| file | role |
|---|---|
| `gatesplan.py` | what runs: every job and `run:` step of gates.yml at the commit, read from git; each `uses:` resolved through the substitution table, anything unmodelled refused |
| `backend_local.py` | where and how: one job at a time on this machine, in a fresh worktree |
| `bundle.py` | what it means: schema whitelist, leak filter, and `complete` |
| `runner.py` | when: schedules jobs up to `--jobs`, honours `needs:`, writes `summary.json` and `bundle.json` |
| `run.sh` | the entry point |
| `allowed_signers` | the one public key (identity and namespace `dawn-gates`) a signed bundle is verified against |
| `publish.py` | refuses an invalid or incomplete bundle, signs it, writes the note on `refs/notes/gates`, pushes it, dispatches `verify-external.yml` |
| `verify_note.py` | reads the note, checks the signature and then the bundle against the commit through `bundle.check`, the code `bundle.py verify` runs |
| `release_evidence.py` | release.yml's `verified` verdict: a green ci.yml run at the tag's sha, or a `gates/maintainer` success written by verify-external.yml on the default branch (the header lists every check); `--selftest` runs first on every tag |
| `steps_lock.py`, `steps.lock.json` | the checked-in expectation of every job family's `run:` steps (issue #167); tree-policy runs `check` on every push |

## The steps lock: removing a step has to be said

`bundle.py` compares what an external run executed with gates.yml at the
same commit, so it cannot see gates.yml itself losing a step, and neither
can any check that compares gates.yml with itself. `steps.lock.json` is the
other side of that comparison: for each job family (a job id with one
trailing `-<digits>` removed, so `contracts-1` and `contracts-2` are
`contracts`), the multiset of its `run:` texts, read with `gatesplan.parse`.

```bash
python3 scripts/gates-external/steps_lock.py check     # tree-policy runs this
python3 scripts/gates-external/steps_lock.py selftest  # and this
python3 scripts/gates-external/steps_lock.py record    # by hand, see below
```

`check` is red when a family has a command the lock does not, or lacks one
the lock has, and names the family and the command. Moving a command between
shards of one family is a reshard and stays green; moving it to another
family is red on both sides.

This is a gate you are expected to open on purpose. When a commit removes,
adds or moves a run step across families, that same commit runs `record` and
commits the rewritten lock, and its message says why. The lock's diff is then
where a reviewer reads which command went; a commit that changes gates.yml's
steps without touching the lock is red. A gates.yml that `gatesplan.py`
refuses is red here too, which is also the moment the external runner could
no longer run it.

## The backend contract

Given a tree and inputs, run the command list in order, and return an exit
code and an output digest per command. A backend is a module
`backend_<name>.py` with `create(ctx)`, where `ctx` carries `repo`, `tree`,
`out`, `options` (from `--backend-opt KEY=VALUE`) and `log`. The object it
returns implements:

| method | meaning |
|---|---|
| `prepare()` | once, before any job |
| `run_job(job, artifacts)` | run one planned job (its ordered actions: `run` steps and `use` steps carrying a replacement id, each with an optional step `id` and `if`; plus `needs_results` from the runner). Expressions and step conditions are evaluated with `gatesplan.expand` and `gatesplan.step_condition_holds`; `artifacts` is the run's artifact store. Returns `{"steps": [...], "ok": bool}`, one step dict per `run` action with `executed`, `exit_code`, `stdout_sha256`, `stderr_sha256` |
| `toolchain()` | the bundle's toolchain fields |
| `cleanup()` | once, after every job |

A backend decides where things run and how each replacement id is realised.
It does not decide what runs (`gatesplan.py`) or what counts as complete
(`bundle.py`). Adding a backend adds a file and changes none.

## The substitution table

| `uses:` / adjustment | replacement id | local meaning |
|---|---|---|
| `plan` (the #168 job) | `external-all` | not executed: an external run is the full set, so each gate job's condition on the plan's outputs is taken as satisfied. Only the exact wiring #168 wrote is accepted (the job naming itself); any other condition is refused |
| `actions/checkout@v4` | `tree-worktree` | `git worktree add --detach <sha>` in a fresh directory per job. Full history and tags are present even where CI checks out at depth 1 |
| `./.github/actions/dawn-toolchain` | `dawn-toolchain-local` | JDK 21 on `JAVA_HOME` and `PATH`; the seed cache copied in from the shared cache (seedjar.sh re-verifies it); `./bin/dawn --version` unless `build: 'false'`. The composite's `action.yml` is fingerprinted at the same commit and a changed composite is refused |
| `actions/cache@v4` | `noop` | nothing saved; the restore half is the seed-cache copy above, and coursier's cache is the user's own |
| `actions/upload-artifact@v4` | `artifact-store-local` | copied to `<out>/artifacts/<name>`; `if-no-files-found: error` is honoured |
| `actions/download-artifact@v4` | `artifact-fetch-local` | every artifact matching `pattern` copied to `<path>/<name>` |
| `actions/setup-node@v4` | `node-host` | the host's `node`, whose version is recorded in the bundle |
| `actions/setup-java@v4` | `jdk21-host` | the same local JDK 21 (GraalVM CE, not Temurin); only `java-version: '21'` is accepted |
| `adjust:runner-temp` | `per-job-directory` | `RUNNER_TEMP` and `${{ runner.temp }}` point at a per-job directory outside the worktree |
| `adjust:tmpdir` | `per-job-directory` | `TMPDIR` is per job |
| `adjust:literal-tmp-paths` | `machine-wide-lock` | a step naming a literal `/tmp/<name>` path holds a lock on it, so two runs of this script cannot share it |
| `adjust:playground-port` | `free-port-per-run` | `PLAY_TEST_PORT` is a free port, not 8097 |
| `adjust:github-env-files` | `per-step-files` | `GITHUB_ENV`, `GITHUB_PATH`, `GITHUB_OUTPUT`, `GITHUB_STEP_SUMMARY` are per-step files, and ENV/PATH carry to later steps |

A `uses:` reference not in this table (including a version bump of one that
is) makes `run.sh` refuse before any job starts.

## The bundle

Fields: `tree`, `gates_blob`, `substitutions[]` (`subject`, `replacement`),
`steps[]` (`job`, `name`, `command`, `exit_code`, `stdout_sha256`,
`stderr_sha256`, `executed`), `toolchain` (`seed_jar_sha256`, `java`, `cc`,
`python`, `node`), `complete`. Nothing else, at any depth.

`complete` is true only when the multiset of (job, run text) over every run
step of gates.yml at `tree` equals the multiset over executed steps, and every
executed step exited 0. `bundle.py verify` recomputes it from git and ignores
the bundle's own claim.

Logs, timings and the memory peak stay in `<out>/logs` and
`<out>/summary.json`, which are for the person who ran it and are not evidence.

## Signed evidence

The note on a commit in `refs/notes/gates` is an envelope with exactly two
fields, `{"bundle": <bundle>, "signature": "<armored SSH signature>"}`. The
signature is `ssh-keygen -Y sign -n dawn-gates` over the bundle's canonical
bytes, `json.dumps(bundle, sort_keys=True, separators=(",", ":"))` plus a
newline, so the envelope's own formatting does not matter.

`verify-external.yml` takes the verifier and `allowed_signers` from the default
branch and reads the commit under test as objects only, so a commit cannot
vouch for itself. It writes the commit status `gates/maintainer`: `success`
when every item of `verify_note.py`'s checklist holds, `failure` otherwise.
Anyone can repeat the check:

```bash
git fetch origin refs/notes/gates:refs/notes/gates
python3 scripts/gates-external/verify_note.py --sha <sha>
```

What nobody but the key holder can vouch for is that the steps really ran;
the exit codes and output digests are the maintainer's statement. The
protocol and its limits are in [docs/bootstrap.md](../../docs/bootstrap.md)
(Chinese).
