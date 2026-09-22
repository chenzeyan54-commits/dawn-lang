# Prepared loader invocation counts

The host observer counts real parser, index and token-projection method entries
using the same pinned emitted descriptors as the source API counter. It does
not substitute reported cache statistics for executed work.

Project samples construct a captured plan and overlays for three real loaded
modules. The full loader interval must parse each module once: capture-on has
three indexes/projections, while cold loading has none. An explicit standalone
sample includes its preparation, which must parse/index/project once.

The Session consumer sample prepares std and source outside an explicitly marked
measurement interval in the external fixture. It then calls the actual prepared
Session API and must execute zero parsers/index builders/projections. This last
sample proves the consumer does not reparse; it does not hide loader cost in the
project and standalone samples, and is not end-to-end latency evidence.

Independently compiled duplicate seed/dependency parsing, eager cold capture,
and prepared-consumer reparsing controls must preserve semantic samples and fail
their exact count vectors. Instrumented and uninstrumented runs both check the
fixtures' semantic conditions. Host code stays outside the portable compiler.
