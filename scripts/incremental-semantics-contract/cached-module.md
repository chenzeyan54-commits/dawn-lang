# Cached module transition controls

`python3 scripts/incremental-semantics-contract/cached-module.py` runs independent
positive subjects followed by nine compiling mutations of the actual
`driver/analyze` implementation in a temporary copy. `--suite driver` selects the
seven transition controls; `--suite observer` selects the two host-boundary
controls. `--only NAME` narrows driver controls but keeps their positive.
`--self-test` checks every exact source anchor without compiling.
The driver suite also accepts `--shards N --shard I` (zero-based), applying
deterministic modulo partitioning after `--only` selection. Empty partitions are
rejected and every partition retains an independent positive. Three driver
partitions contain 3/2/2 controls; the separate observer suite contains two,
covering all nine exactly once. Self-tests verify this coverage and reject
invalid partitions and false failure evidence.

The transition controls pin reuse counts, module ownership, source binding,
FFI refusal, error-cache eviction, fresh comptime evaluation, and current carry
publication. A negative must fail its named assertion owner, not compilation,
linkage, or a panic. Each subject reports elapsed wall time, including compilation
and its test dependency closure; these are gate-cost measurements, not replay
speedup measurements.

The external `src/cached_module_observer.dawn` fixture owns mutable Java state outside
the portable compiler. It observes the exact ordered module/check/comptime
intervals on both cold and warm transitions, compares semantic products, and
checks that a tooling query after a warm hit reaches the current host oracle
without touching the previous generation's oracle. A warm hit must occur before
the host assertions are meaningful. The fixture does not put Java imports in
`selfhost` and does not duplicate the semantic engine.

These controls are intentionally not wired into CI here. Scheduling must account
for measured full dependency-closure costs; adding this script to an existing
job without measuring its headroom is not part of this change.
