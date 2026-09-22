# Real Playground gateway Session contract (JVM)

This test composes the unchanged `playground/lsp_gateway.py`, its existing
WebSocket test client, and explicitly selected configured JVM compiler children.
It does not replace the existing fake-child security tests. It does not exercise
the HTTP `/check` worker, the native sandbox, deployment, or default activation.

The command accepts a JSON subject manifest with four entries: `cold-plain`,
`prepared-plain`, `cold-stats`, and `prepared-stats`. Each entry names an explicit
`argv` ending in `-jar <compiler.jar> lsp` and the configured builder's `metadata`
file. The runner checks policy, observer mode, compiler hash, and identical source
fingerprints; it records executable and adjacent dependency-jar hashes. No implicit
`/tmp` discovery or silent artifact reuse is permitted. Existing artifacts used
for development must be labeled `--evidence-label local-smoke`.

For each subject, one real loopback gateway owns two simultaneous WebSocket
clients. Both use the production fixed scratch URI but disjoint identifier sets.
The existing ten-revision standalone corpus supplies body/signature/reorder/
deletion/insertion/error/recovery/identical edits. Actual publications must have
the right URI and current version. Complete diagnostics, hover, definition and
completion results compare across all four subjects. Prepared stats must match
the existing exact standalone count oracle; cold stats remain explicitly
unobserved. Queries of an idle peer must neither change its replies nor produce
new analysis counts. Closing and reconnecting one client must produce a fresh
child and full cold initial body counts without affecting the surviving peer.

An exec-only private child wrapper redirects its own stderr to an exclusive
per-PID file and then `os.execvpe`s the actual compiler, retaining the PID. It
does not parse or synthesize protocol messages. The runner links the new PID to
the gateway's actual `child-start` session record before assigning it to a client,
then verifies the live process command line. Every counter interval belongs to
one owned log, never a sum across clients. Gateway stderr EOF is intentionally
not part of its FIRST_COMPLETED termination set; no production change is needed.
The wrapper's private logs must never become production gateway logging.

Self-tests reject stale/missing publications, wrong URIs, missing/duplicated or
borrowed counters, duplicate child ownership, and mismatched policy/hash inputs.
Canonical output retains source hashes, client/PID/session ownership, subject
provenance, full semantic samples, child logs and gateway logs. All sockets and
children have bounded cleanup. JVM evidence remains explicitly JVM-labeled;
native memory/lifecycle and the literal `/check` requirement remain open.

Subject manifest shape (supply actual absolute paths; the four metadata files
come from `lsp-configured.py` builds with their corresponding policies):

```json
{
  "cold-plain": {"argv": ["/path/to/jdk/bin/java", "-Xss512m", "-Xmx2g", "-jar", "/artifacts/cold-plain/compiler.jar", "lsp"], "metadata": "/artifacts/cold-plain/metadata.json"},
  "prepared-plain": {"argv": ["/path/to/jdk/bin/java", "-Xss512m", "-Xmx2g", "-jar", "/artifacts/prepared-plain/compiler.jar", "lsp"], "metadata": "/artifacts/prepared-plain/metadata.json"},
  "cold-stats": {"argv": ["/path/to/jdk/bin/java", "-Xss512m", "-Xmx2g", "-jar", "/artifacts/cold-stats/compiler.jar", "lsp"], "metadata": "/artifacts/cold-stats/metadata.json"},
  "prepared-stats": {"argv": ["/path/to/jdk/bin/java", "-Xss512m", "-Xmx2g", "-jar", "/artifacts/prepared-stats/compiler.jar", "lsp"], "metadata": "/artifacts/prepared-stats/metadata.json"}
}
```

The runner is Linux-only for its `/proc` child identity check. `--self-test`
launches no compiler. A normal run may have two JVM children alive at once by
design; coordinate that resource demand separately from native acceptance.
