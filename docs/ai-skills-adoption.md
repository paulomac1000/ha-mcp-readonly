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

The pinned capability-manifest JSON schema and the narrative capability-manifest
reference currently use partially different field vocabularies. This consumer treats
the canonical JSON schema as the executable serialization contract and records the
narrative-only concepts through reviewed extension fields where possible. The
discrepancy is an upstream residual risk and must be resolved by ai-skills before a
final certification claims that the two sources are literally identical.

## Known residual risk

The new runtime policy surface and context runtime modules are strict-mypy gates.
Older monolithic context analyzers/formatters/utilities and `ha_graph` still contain
pre-existing typing debt under a narrowly scoped override. That debt is not represented
as complete repository-wide strict typing and should be retired incrementally.
