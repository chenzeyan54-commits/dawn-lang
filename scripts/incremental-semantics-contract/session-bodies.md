# Session body-cache controls

`python3 scripts/incremental-semantics-contract/session-bodies.py` runs the
external `src/session_bodies.dawn` fixture against private copies of the actual
`driver/incremental` implementation. Thirteen compiling mutations cover actual
body counts, current-generation replacement, canonical prefix identity,
duplicate identities, eviction, persistent disablement, four shared-capacity
charges, the product limit, and error/Java barriers.

Every negative must reach its named assertion owner. Compilation errors,
linkage failures, panics, a different failing test, or a successful exit cannot
stand in for that assertion. The positive must run all selected owners.

`--shards N --shard I` applies zero-based deterministic modulo partitioning;
each nonempty partition has its own independent positive. `--only NAME` may be
repeated and selects controls before partitioning. The default runs all thirteen.
`--self-test` checks unique source anchors, exact 5/4/4 coverage across three
partitions, independent positives, invalid/empty partitions, and rejected false
failure evidence without compiling.

Each subject reports real elapsed time including compilation and the complete
test dependency closure. These numbers size gates, not semantic-engine speedups.
No CI placement is introduced here; placement needs measured headroom.
