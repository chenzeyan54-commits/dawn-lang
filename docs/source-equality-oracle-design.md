# Source equality oracle coverage

> Status: current. Regression oracles for existing raw source equality; no index optimization is implemented here.

Repeated declaration equality currently seeks through raw source strings.
Before changing that implementation, preserve its observable contract with
an independent oracle built from the existing raw-string operations.

The tests enumerate small half-open code-point ranges, including negative,
reversed, out-of-bounds and empty ranges. Sources contain ASCII, BMP and
supplementary characters, combining marks, comments and line endings. These
checks are independent of declaration/token pairing: `same_text` also accepts
valid ranges that cut tokens or contain no tokens.

Equal-length trivia mutations receive separate examples so replacing raw
comparison with token equality cannot satisfy the oracle. No timing assertion
is made; performance attribution and the original Scale workload remain
separate acceptance work.

## Initial verification

On JDK 21 with the normal seed-backed launcher, targeted
`dawn test selfhost/src/check/source_projection.dawn` passes all 40 tests.
A temporary compiling mutant that rejects valid empty ranges fails exactly
the two new tests; the original implementation is restored afterward.
These checks establish oracle coverage, not an optimized implementation or
performance improvement.

The same 40 targeted tests also pass on the native backend using a freshly
built cold-configured compiler and its matching staged standard library.

## Not included

- No source index or cache implementation change.
- No relaxation of trusted from-parts constructor preconditions.
- No backend-specific malformed Unicode construction in shared Dawn tests.
- No default activation, deployment or phase acceptance claim.
