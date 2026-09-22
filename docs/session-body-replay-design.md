# Session-owned body replay

This is the production integration work following the opt-in body executor,
single-pass renewal, and primitive inferred publication. It is not a phase-5
acceptance report. No speedup is asserted: the repaired production benchmarks
and end-to-end measurements must establish the value gate before activation.

## One module transition

Keep `analyze_module_step` and its recorded counterpart as wrappers around one
implementation. Add an explicitly cached transition returning the ordinary
`ModuleStep`, an optional opaque body cache, and execution counts. The cache
contains only an owner/module identity and `scalar_replay.Admitted`: never a
historical `Cx`, `ModuleHeaders`, Java probe closure, or complete recording.

Every changed module must reconstruct its context and run the canonical header
passes from the current `AnalysisCarry`. The existing standard-library identity
correction and `impls_before` baseline remain authoritative. The cached branch
belongs exactly where the recorded branch currently executes module bodies.
On its first revision, record once and admit; on later revisions, invoke
`replay_and_record` once. Always assemble the current pass and continue through
the existing comptime, diagnostics, exports, implementation-table fold, identity
table, and declaration-span publication. Never restore the old module suffix.

Parse failure, unavailable provenance, changed owner identity, unsupported
snapshot binding, and disabled caching select the canonical cold branch. Header
or body errors cannot borrow an old success. Cache renewal must describe the
actual current execution; it must not union entries with an older generation.
The completed checker context used by the query Program keeps the original Java
oracle, not the temporary observation counter used during this execution.

## Loader and source binding

There is a concrete integration constraint, not just an API mismatch:
`source_snapshot.of(text)` stores the parsed source tree, while the loader's
`qualify_uses` and `apply_rewrites` may replace `DUseModule` path segments before
headers run. The current `snapshot_matches` requires equality of the entire
module. Passing an unmodified snapshot for that resolved module therefore
correctly refuses admission, but would leave package imports without reuse.

Do not weaken the equality check globally or accept arbitrary caller-supplied
AST/text pairs. Introduce a narrowly checked source-resolution binding: compare
every declaration, permitting only import path-segment replacement, preserving
declaration count, order, import selection/alias, and all source coordinates.
All non-import declarations, including every body, must remain exactly equal.
The resulting snapshot retains the same indexed text and tokens and the verified
resolved syntax. The actual header/query facts continue to establish whether
the newly resolved imports mean the same thing. Negative controls must reject
changed bodies, spans, declaration order, and import aliases/selections.

Initially the driver can construct this snapshot explicitly from current text;
that duplicate parse must be included in session measurements, not hidden in a
body-only timer. The final loader integration should produce an optional clean
snapshot beside its existing parse result, propagate the checked import rewrite,
and preserve recovered syntax/diagnostics without reparsing. Cold CLI callers
must not pay for an unused token index. This loader work is required before
claiming efficient production integration.

## Session ownership and eviction

Keep exact-prefix module hits as their existing distinct optimization. Add a
separate bounded current-generation module-to-body-cache table inside the opaque
session. A prefix mismatch ends whole-module reuse, but does not prevent current
headers from revalidating a later module's body products. A changed upstream
implementation can leave runtime callers reusable while comptime executes again;
a changed signature must invalidate affected consumers.

The returned session replaces, rather than appends to, the previous revision's
table. Deleted modules disappear; evict drops prefix and body caches together.
Bound module count, source units, and admitted product count. Count prefix hits,
cold module transitions, comptime transitions, cold bodies, reused bodies,
unadmitted/rejected bodies, capture refusals, and retained products separately.
Changing the captured project plan, standard library, options, or Java lease
constructs a new owner. FFI runs and unproven Java queries must not retain an
apparently pure prefix. Eviction and the explicit cold switch affect only speed.

## Consumers and acceptance

Project LSP sessions already own `driver/incremental.Session`; standalone and
untitled buffers currently call cold `analyze_standalone`. Give those buffers an
owner with the same lifecycle, preserving path checks, standard-library identity,
document version, lease disposal, and stale-result rejection. Normal Playground
editing uses this persistent LSP path. Do not invent cross-request identity or
global caching for the stateless HTTP `/check` fallback.

Before enabling the new path, require:

1. Multiple-revision module and session cold equivalence: rendered diagnostics,
   typed trees, symbols, export/impl/identity carry, source views, and comptime.
2. Real execution counts for body edits, whitespace, declaration insertion,
   deletion and reorder, inferred signature changes, error/recovery, renamed
   imports, dependency changes, duplicate module identities, and cache eviction.
3. Compiling negative controls proving that missing guards/counts are detected.
4. LSP hover, definition, completion, diagnostics and lifecycle equivalence for
   project, standalone, and Playground-style untitled documents.
5. Full incremental contracts, reviewed Core hashes without widened
   normalization, JVM/native differentials, bootstrap fixed points, and docs.
6. Production benchmark counts/equivalence outside timers and session p50/p95,
   CPU, hit-rate, and retained-memory evidence. Body-only timings do not prove
   end-to-end speedup. The existing calls/inferred/generic value gate remains.

## Non-goals and remaining scope

This integration does not make the currently conservative body shape admit all
language constructs. Generic dictionaries, methods, defaults, tests, constants,
closures, Java and comptime dependency handling still require their planned
work. No disk/global cache, incremental parser, checker parallelism, new language
semantics, backend ABI change, or deployment is included. Phase-5 and phase-7
reports remain contingent on their full original acceptance requirements.
