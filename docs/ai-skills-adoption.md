---
description: Status and evidence policy for adoption of the pinned ai-skills contracts.
doc_id: reference.ai-skills-adoption
type: reference
status: active
rigor: normative
owners: [repository-maintainers]
verification: Compare the pinned revision with CI configuration and generate provider-backed adoption evidence only after the assessed GitHub revision and its checks exist.
---

# ai-skills adoption status

The repository targets the immutable `paulomac1000/ai-skills` revision
`b54fc6b27ea80b36a70d5de73445970e17f55789` from the `fix/unified-contract-release-hardening` line.
The revision immediately follows the previous `c5ba4091` pin and changes only the
.NET generator lock validation lane; the Python/FastMCP normative entrypoints and
their recorded content digests are unchanged. The consumer lock, CI checkout, and
migration evidence must all use the same immutable revision.

This repository implements the reviewed migration code, but implementation is not
the same thing as provider-backed acceptance. A canonical adoption assessment is
evidence about an already-existing immutable GitHub revision, so final assessment
data is generated as a CI/review artifact after the exact commit exists rather than
attempting to predict the SHA of the commit containing the assessment itself.

## Evidence policy

Final L3 acceptance must use the canonical adoption schema and validator from the
pinned ai-skills revision. Evidence must bind the assessed revision, workflow run,
job/check IDs, exact wheel/container artifacts and digests, official-client transport
results, live Home Assistant integration results, residual risks, rollback procedure,
and an independent GitHub review to the same SHA.

The public CI lanes provide deterministic source, wheel, container, security, and
official-client evidence without requiring a private Home Assistant instance. Live
Home Assistant smoke, E2E, and integration evidence remains a separate required lane
and must be attached before the final decision can become `approve`.

Until that provider-backed assessment and independent review exist, the adoption
decision remains **request changes / not certified**. This is an evidence-state
statement, not a claim that the implementation should be rolled back.

## Upstream normative vocabulary conflict

The pinned capability-manifest JSON schema is the executable serialization contract.
Its `operation_kind` is this consumer's side-effect projection; `risk`, `impact`,
`determinism`, `latency`, retry/idempotency booleans, `authorization_scopes`,
`concurrency`, and `max_response_bytes` are validated at load time. Narrative-only
axes are represented explicitly where the schema has no top-level field:
`extensions.data_classification` carries confidentiality, `extensions.cost` carries
cost, `extensions.target_binding` carries stable target identity/revalidation,
`extensions.retry_conditions` and `extensions.idempotency_mechanism` carry retry and
idempotency evidence, and `extensions.outcome_semantics` carries ambiguous/unknown
outcome handling. The invocation kernel enforces authorization scopes, target binding,
deadlines, concurrency, and response bounds; descriptive confidentiality/cost fields
are not independent authorization grants.

The narrative operational-impact vocabulary (`transient`, `persistent`, `outage`,
`safety-critical`, `financial`) and explicit abuse-potential axis do not have literal
schema equivalents in the pinned contract. They are therefore an upstream residual
risk rather than silently being equated with the schema's `impact` enum. Final
certification must not claim literal schema/narrative identity until ai-skills resolves
that mismatch or the consumer records reviewed extensions for every missing axis.

## Known residual risk

The new runtime policy surface and context runtime modules are strict-mypy gates.
Older monolithic context analyzers/formatters/utilities and `ha_graph` still contain
pre-existing typing debt under a narrowly scoped override. That debt is not represented
as complete repository-wide strict typing and should be retired incrementally.
