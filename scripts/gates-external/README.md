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

# inside a prefix: the pinned toolchain and inputs, an environment built from nothing
scripts/gates-external/inputs.py build --prefix ~/dawn-gates      # download, check, lay out
scripts/gates-external/inputs.py verify --prefix ~/dawn-gates     # re-hash everything
scripts/gates-external/run.sh --sha <sha> --backend local --prefix ~/dawn-gates [--only ...]
scripts/gates-external/prefix.py check-isolation --prefix ~/dawn-gates \
    --marker ~/dawn-gates/tmp/marker [--readonly-root] -- <command>
scripts/gates-external/prefix.py selftest --prefix ~/dawn-gates [--break-env-i]

# on the cluster, from a local prefix that holds the input pack
scripts/gates-external/run.sh --sha <sha> --backend crun --prefix ~/dawn-gates --jobs 16 \
    --backend-opt remote-prefix=<cluster dir> [--backend-opt isolation=1] [--only ...] \
    [--backend-opt run-as=UID:GID|root] [--backend-opt private-tmp=0]
```

Exit status of `run.sh`: 0 complete, 1 ran but not complete, 2 refused to
plan, 3 the bundle was refused (leak or schema), nothing written.

## Files

| file | role |
|---|---|
| `gatesplan.py` | what runs: every job and `run:` step of gates.yml at the commit, read from git; each `uses:` resolved through the substitution table, anything unmodelled refused |
| `backend_local.py` | where and how: one job at a time on this machine, in a fresh worktree (a fresh clone inside a prefix) |
| `backend_crun.py` | where and how, on the cluster: ships the input pack and the tools once, then one zero-card `crun run` per job, each running `prefix.py run-job` (the local backend in prefix mode) inside the cluster's prefix |
| `bundle.py` | what it means: schema whitelist, leak filter, and `complete` |
| `runner.py` | when: schedules jobs up to `--jobs`, honours `needs:`, writes `summary.json` and `bundle.json` |
| `run.sh` | the entry point |
| `prefix.py` | the prefix layout, the whitelist environment every prefix job gets, `check-isolation`, and `run-job` (a crun job's remote half) |
| `inputs.py` | the offline input pack: download, check against `inputs.lock.json`, lay out, `verify` |
| `inputs.lock.json` | name, version, URL and sha256 of every download the prefix holds |
| `allowed_signers` | the one public key (identity and namespace `dawn-gates`) a signed bundle is verified against |
| `publish.py` | refuses an invalid or incomplete bundle, signs it, writes the note on `refs/notes/gates`, pushes it, dispatches `verify-external.yml` |
| `verify_note.py` | reads the note, checks the signature and then the bundle against the commit through `bundle.check`, the code `bundle.py verify` runs |

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

## The prefix

`--prefix DIR` runs every job inside one directory. Its layout is in
`prefix.py`'s docstring: `toolchain/` (GraalVM CE 21.0.2, node 20, wasi-sdk 34,
python 3.12.3), `inputs/` (the archives, the seed jar and std, a coursier
cache, `MANIFEST.json`), `jobs/<sha>/`, `home/`, `tmp/`, `cache/`,
`out/<sha>/`. No location is written into the code.

A prefix job's environment is not the caller's minus a drop list; it is built
from nothing (the effect of `env -i`): `PATH` is the toolchain bins then
`/usr/bin:/bin`, `JAVA_HOME` and `GRAALVM_HOME` the prefix's GraalVM, `HOME`,
`TMPDIR`, `RUNNER_TEMP`, `XDG_CACHE_HOME` and `COURSIER_CACHE` under the prefix,
`LANG=C.UTF-8`, `CI=true`, plus the per-job `GITHUB_*` values the local
backend already sets. `DAWN_SEED` is not set: CI does not set it, and it makes
`seedjar.sh` skip its checksum. The seed reaches a job the way the cache
restore does, copied into `.dawn/seeds`. A job's checkout is a
`git clone --shared` under the prefix, not a worktree, because a worktree
writes into the source repository's `.git`.

One change is made to an unpacked toolchain: GraalVM's `bin/java` is a shim
that execs `bin/java.real` with `-XX:-UsePerfData` first, because HotSpot
writes `/tmp/hsperfdata_<user>` for every JVM whatever `TMPDIR` says, and the
environment variables that could carry the flag print `Picked up ...` on
stderr. The lock entry is the archive and does not change; `verify` checks the
shim's bytes and that `java.real` is the archive's `bin/java`.

`inputs.py` trusts only the digests in `inputs.lock.json`. The seed jar and std
are checked against `scripts/seed-checksums.txt` and `seed-std-checksums.txt`,
the tables `seedjar.sh` reads, and the coursier jars against
`selfhost/dawn.lock`. Downloads are not in the repository; their digests are.

`check-isolation` touches a marker in the prefix, runs the command, then lists
every path outside the prefix (on `/` and on the prefix's filesystem, `-xdev`,
pseudo filesystems pruned) whose mtime or ctime is newer. `--exclude` names
paths other processes write (a shared workstation has several); they are
printed with the result. `--readonly-root` runs the command under bubblewrap
with everything but the prefix read-only, so a write outside fails the
command instead of waiting to be found.

Without `--prefix` nothing changes: the host-environment path of the first
knife is kept as it was.

On the cluster a container gives root and nothing else, while CI runs every
job as an ordinary user, and two contracts refuse root (root reads a
`chmod 000` file and writes an unwritable directory). So `prefix.py run-job`
starts as root, hands the writable part of the prefix (`home/`, `tmp/`,
`cache/`, `repos/<sha>.git`, `jobs/<sha>`, `out/<sha>`) to uid 20000, and
re-executes itself through `setpriv --reuid --regid --clear-groups
--no-new-privs`. `toolchain/` and `inputs/` stay root's, so a job cannot
change what it is measured with. A uid change does not close `/tmp`,
`/var/tmp` and `/dev/shm`, which anyone may write, so the job also gets a
private mount namespace in which each is a per-job directory in the prefix
(what a fresh CI VM gives a job; a JVM's `java.io.tmpdir` ignores `TMPDIR`).
`run-as=root` is the negative control; `private-tmp=0` keeps the shared ones.

## The substitution table

| `uses:` / adjustment | replacement id | local meaning |
|---|---|---|
| `plan` (the #168 job) | `external-all` | not executed: an external run is the full set, so each gate job's condition on the plan's outputs is taken as satisfied. Only the exact wiring #168 wrote is accepted (the job naming itself); any other condition is refused |
| `actions/checkout@v4` | `tree-worktree` | `git worktree add --detach <sha>` in a fresh directory per job. Full history and tags are present even where CI checks out at depth 1 |
| `./.github/actions/dawn-toolchain` | `dawn-toolchain-local` | JDK 21 on `JAVA_HOME` and `PATH`; the seed cache copied in from the shared cache (seedjar.sh re-verifies it); `./bin/dawn --version` unless `build: 'false'`. The composite's `action.yml` is fingerprinted at the same commit and a changed composite is refused |
| `actions/cache@v4` | `noop` | nothing saved; the restore half is the seed-cache copy above, and coursier's cache is the user's own |
| `actions/upload-artifact@v4` | `artifact-store-local` | copied to `<out>/artifacts/<name>`; `if-no-files-found: error` is honoured |
| `actions/download-artifact@v4` | `artifact-fetch-local` | every artifact matching `pattern` copied to `<path>/<name>` |
| `actions/setup-node@v4` | `node-host` | the host's `node` (the prefix's node 20 under `--prefix`), whose version is recorded in the bundle |
| `actions/setup-java@v4` | `jdk21-host` | the same local JDK 21 (GraalVM CE, not Temurin; the prefix's under `--prefix`); only `java-version: '21'` is accepted |
| `adjust:runner-temp` | `per-job-directory` | `RUNNER_TEMP` and `${{ runner.temp }}` point at a per-job directory outside the worktree |
| `adjust:tmpdir` | `per-job-directory` | `TMPDIR` is per job |
| `adjust:literal-tmp-paths` | `machine-wide-lock` | a step naming a literal `/tmp/<name>` path holds a lock on it, so two runs of this script cannot share it |
| `adjust:playground-port` | `free-port-per-run` | `PLAY_TEST_PORT` is a free port, not 8097 |
| `adjust:github-env-files` | `per-step-files` | `GITHUB_ENV`, `GITHUB_PATH`, `GITHUB_OUTPUT`, `GITHUB_STEP_SUMMARY` are per-step files, and ENV/PATH carry to later steps |
| `adjust:wasi-sdk-tarball` | `input-pack-tarball` | under `--prefix`, `WASI_SDK_TARBALL` names the input pack's wasi-sdk archive, which wasm-target's step copies instead of downloading; the step's pinned sha256 is checked either way. Listed only for a commit whose gates.yml reads the variable; without `--prefix` it is not set and the step downloads |

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
