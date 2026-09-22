# Bounded generic dictionary admission: trace-first slice

> Status: current. Production refusal baseline and proposed entry proof; admission remains unchanged.

This is an M3.3 prerequisite, not a new admission class or a completed G3 gate.
The original workload remains `Scale[T]`: a nominal `Coin` implementation and
explicit-return generic functions making three dictionary-dispatched calls.
Unbounded identity functions are not a substitute. Additional fixtures use two
bounds and two bounded type parameters to expose dictionary ordering and key
axis mistakes. Production recording, admission, replay, and renewal remain
unchanged in this slice.

## Baseline contract

Capture each fixture through `body_execution.record`, then use
`scalar_replay.admit`, `replay`, and `replay_and_record` over repeated, shifted,
and reordered revisions. Count actual canonical checks independently of the
reported counters. Bound functions and implementation bodies must currently
remain cold: capture success does not imply admission. Compare every mutable
body product and checker context field against fresh cold execution, excluding
Java capability equality only when identity is preserved. Inspect every saved
read, write, typed tree, symbol, bound delta, and dictionary frame through a
private reflection trace rather than introducing broad printing dictionaries
into the compiler.

## Candidate entry proof to review before admission

The proposed helper takes the candidate scheduler entry `Cx`, exact candidate
declaration and signature, and projected saved product. It returns a temporary
validation context plus an entry proof, or refuses. It must derive each hidden
dictionary from candidate signature type-parameter/bound order and the
candidate declaration's allocation slots, not from the saved frame.

For each bound slot prove the exact binder identity, current trait name,
`(binder, trait)` frame key, `(trait, binder)` symbol tag, hidden symbol spelling,
symbol type/owner/source location, and ordered `TFun.dict_syms` entry. Check
parameter and dictionary allocations against the authoritative checker entry
contract. Preserve all unrelated bound rows while applying this function's
bound delta. Require empty evidence for this pure slice. Do not execute entry
twice or consume candidate slots while merely validating a candidate.

The trace must determine which scoped reads need this reconstructed context
and which observations involve temporary inference substitutions. Revalidate
each before/after unification fact, candidate namespace, and trait fact in its
proper scope. Dictionary call witnesses need explicit correspondence to
validated entry dictionaries; expression shape alone is insufficient. No new
fact family becomes shared memo evidence in this slice.

## Deferred work

Production guard changes require trace review and independent compiling
negative controls. Effect evidence, inferred generic signatures, associated
outputs, defaults, lambdas, and wider nominal values remain separate classes.
G3 still requires the original 1000-function generic workload, truthful cold
implementation-body denominators, real edit hit rates, and replay per-body
cost below cold checking. No speedup follows from this refusal baseline.

## Observed production baseline

The private `scripts/incremental-semantics-contract/generic-trace.py` fixture
retains two original `g0`/`g1` functions, each making the benchmark's three
`scale` calls. The additional functions call `Scale`/`Stamp` through two bounds
on one binder, and `Scale` through two distinct binders. All remain pure and
explicit-return; their nominal implementation bodies remain in the denominator.

| Fixture | Generic functions | Implementation bodies | Actual cold checks per pass | Reused |
| --- | ---: | ---: | ---: | ---: |
| Original Scale | 2 | 1 | 3 | 0 |
| Multiple bounds | 1 | 2 | 3 | 0 |
| Two parameters | 1 | 1 | 2 | 0 |

Each history checks recording, unchanged replay, shifted renewal, and another
renewal (including declaration reorder for the original workload). All twelve
full body/context comparisons agree with independent cold execution; recording
and renewal have zero capture refusals. The baseline is refusal by class, not
dependency rejection. The Java oracle instruments canonical checker entries in
a private source copy, so executor counters alone cannot satisfy the contract.

The complete read traces have exactly the following family counts per generic
function. They are pinned by the fixture rather than inferred from AST shape.

| Read family | Original Scale | Multiple bounds | Two parameters |
| --- | ---: | ---: | ---: |
| TraitName | 4 | 5 | 5 |
| FunctionAnswer | 6 | 6 | 6 |
| ReducedAssociatedType | 9 | 8 | 9 |
| ReducedTypeEffects | 9 | 8 | 9 |
| ConcreteType | 6 | 5 | 6 |
| UnifiedTypes | 6 | 5 | 6 |
| DictionarySymbol | 3 | 3 | 3 |
| ReducedEffect | 3 | 3 | 3 |
| AssignableType | 1 | 1 | 1 |

There are no `ParameterBounds`, `FunctionCandidates`, or
`ScopedTypeParameter` reads in these exact expressions. This does not license
dropping those dependencies in other generic forms. The current signature and
header namespace must still be validated. Function answers are canonical trait
method signatures, including the trait's own binder and constraint. Unification
maps that binder to the caller's distinct binder; subsequent primitive argument
and return/effect reductions carry that nonempty map. `ConcreteType` returns
false for the trait binder and true for primitive arguments. Simply permitting
generic types while retaining the empty-substitution guard would refuse these
bodies; dropping substitution validation would be unsafe.

`passes.register_fns` already installs every function's bound rows in headers.
Consequently all four recorded products have an empty net `bounds` delta, but
their write journals retain every `BoundsKey`. Dictionary symbols precede
ordinary parameter symbols after the signature binder slots: one binder plus
one dictionary plus two parameters uses four slots; one binder plus two
dictionaries uses five; two binders plus two dictionaries uses six. The fixture
checks the exact journal, recorded header slot offsets, symbol names/types and
tags, dictionary frame axes, and `TFun.dict_syms` order. No alias/signature or
diagnostic delta is present. All three calls carry `WForward` witnesses; the
multi-bound and two-parameter sequences are first dictionary, second dictionary,
first dictionary. Effect evidence is empty.

## Concrete next helper and integration boundary

Prefer factoring the existing pure `enter_fn` plus ordinary parameter-declare
loop into an authoritative checker helper shared with `check_fn_body`, rather
than independently reimplementing its allocation/spelling rules. A proposed
`function_entry(cx, declaration, signature)` result contains the temporary entry
context and ordered parameter/dictionary/evidence symbol lists. The validation
caller uses the real candidate scheduler context and exact signature, with
defaults and effects excluded for this class. It compares every saved entry
symbol against the helper's independently generated candidate symbol, including
relative name/parameter spans, and refuses entry diagnostics or unexpected
journal/allocation differences.

The helper computes an immutable temporary context: it must not replace the
live candidate state. On a hit, existing product assembly installs the validated
final slot row once; on a miss, canonical cold checking receives the original
candidate context. Do not compare the saved post-body scope stack directly with
the entry stack: `check_fn_body` pops the root scope before saving its product.
Validate the final-frame invariants separately and use the temporary entry
dictionary map for scoped `DictionarySymbol` revalidation.

Trait calls need a separate proof path: `callee_index` deliberately excludes
trait signatures. Preserve that ordinary-call contract. Bind each typed trait
call to its revalidated canonical method answer, derive its type substitution
from candidate operand types, and require each forwarded witness to name the
corresponding candidate entry dictionary in signature constraint order. Do not
accept `WConcrete` or `WApply` merely because the saved tree contains them.
Revalidate all nine observed families, including complete unification
before/after bindings, without extending shared memoization. The current
`checker.revalidate_witness_read` already recomputes these reductions and
unifications and rejects noncanonical duplicate binding maps; class membership
must still restrict the operands to the separately justified bounded shape.

The strengthened baseline ran successfully on 2026-09-22 in 11.03s with the
existing JDK 21 compiler launcher. This is fixture build/validation time, not
generic replay performance. No native, Core-golden, CI, or production admission
change is included.

## Helper-only extraction

The next implementation step exposes `FunctionEntry { cx, parameters }` and
`function_entry(cx, declaration, signature)`. It factors only the existing
`enter_fn` and ordinary parameter declaration loop from `check_fn_body`.
The cold body checker consumes the returned context and parameter IDs; it keeps
its final dictionary and evidence collection in exactly the same position.
In particular, `ev_sym_list` is not moved to entry, because doing so could
change effect-related observation ordering. Default preparation and inferred
signature scheduling are untouched. A separate pure `function_dictionaries`
accessor reuses the existing canonical dictionary ordering only when a proof
caller asks for it; ordinary cold checks gain no extra dictionary traversal.

The helper is a temporary computation over immutable contexts, not an entry
installation API. Tests must call it from real scheduler entry contexts, then
check/replay from the original input and compare every final body/context field.
They must compare the helper's independently produced entry symbols, dictionary
order and slots with captured products, including multiple bounds and distinct
type parameters. The existing frozen ordinary-body loop remains the independent
reference for extraction equivalence, including effect/default cases. No
production admission guard is opened by this extraction.

`FunctionEntry` adds a small return-record allocation to ordinary cold function
entry. This extraction is not claimed to have zero overhead; the production
workload measurements must include it. The minimal record avoids additional
dictionary traversal and derived whole-context equality/printing dictionaries.

The helper-only validation on 2026-09-22 passed 359 focused checker tests and
the formatter check. `scripts/incremental-semantics-contract/function-entry.py`
passed in 14.79s: the original twelve generic body/context pairs remain intact,
and 32 additional full context/tree pairs match the exact frozen pre-extraction
body loop from `f688f4b4`. These cover the three original generic classes plus
scalar, named-effect, declared-IO, ordinary-default, bounded-default,
duplicate-parameter, wrong-default-type and wrong-return-type functions, with
logging both disabled and enabled. The malformed cases must emit diagnostics;
the other cases must not. Two real scalar replay histories still hit after a
private candidate hook computes and discards the temporary entry context.

The host compares returned contexts and trees field by field, not through
generated Dawn equality. Temporary entry contexts are also compared with a
second independent entry from the same scheduler input, and ordered parameter
and dictionary symbol metadata must match the checked body. This is an
extraction oracle, not a second checker: its frozen body loop still calls the
unchanged canonical type/effect/default helpers.

Core review lowered all 107 modules successfully. Only `check.checker.core`
changed: two added helpers and the changed `check_fn_body` prologue. Every other
existing Core function is unchanged. The return-record constructor, field
loads and release are visible in Core; no new equality or printing dictionary
is generated. No golden was re-recorded in this slice. Native and full-suite
integration remain separate acceptance work.
