# Actual source parse invocation counts

Run `python3 scripts/incremental-semantics-contract/source-parse-counts.py` with
JDK 21. The harness reuses ASM 9.7.1 from the existing compiler dependency cache;
`--asm-jar PATH` supplies it explicitly. It does not fetch ASM or add Java imports
to selfhost; fixture compilation uses ordinary repository dependency resolution.

A private external fixture calls the production source API. A host classloader
instruments entries of the actual emitted parser, index constructors and token
projection method. Target class names, descriptors, static dispatch and complete
coverage are pinned: missing/inlined/renamed targets fail closed. Counters reset
after target/fixture class initialization. An independent plain classloader
executes every semantic sample before the instrumented run, including mutants.

Observed `(parse, index, projection)` counts are `(1, 1, 1)` for clean capture and
`of`, and `(1, 0, 0)` for capture disabled, recovered parsing and lexer failure.
Three independently compiled source controls require the exact named count
assertion and exact expected/actual vectors:

- Duplicate parse: clean capture observes `(2, 1, 1)`.
- Eager index: capture disabled observes `(1, 1, 0)`.
- Eager projection: capture disabled observes `(1, 1, 1)`; constructing its
  required index is intentionally counted too, not hidden or subtracted.

Compilation errors, linkage failures, semantic sample failures and panics are
not accepted as count-control failures. Each subject starts from the original
production source. Nothing rewrites the compiler or production artifacts.

This proves invocation counts for this API only. It does not prove that a loader
avoids parsing before calling the API, that an index allocation is cheap, or that
session memory/end-to-end time improved. Prepared-loader integration and original
acceptance gates remain separate. Printed subject timings include fixture build
and the host runs, not production performance samples. No CI routing is added.
