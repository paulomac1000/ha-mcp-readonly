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

The repository currently targets the exact `paulomac1000/ai-skills` revision
`c6dc6b13b2dd40b6e087140cd071b45067d75b39` for compatibility work. This is an
immutable implementation target, not an approval claim. The upstream branch was
not treated as a final acceptance authority while its own exact-head validation
remained unresolved.

The former `migration-assessment.yaml` was removed because it used a superseded
schema, referenced an older ai-skills revision, and made artifact-promotion claims
that were not true for the then-current release workflow. Keeping that file would
have created false compliance evidence.

## Evidence policy

A canonical adoption assessment is evidence about an already-existing immutable
GitHub revision. It therefore belongs in provider-backed CI/review evidence, not in
a source file that attempts to predict the SHA of the commit containing itself.
Final acceptance must use the canonical adoption schema and validator from the
chosen immutable ai-skills revision and bind evidence to the exact assessed
revision, workflow run, job/check IDs, artifacts/digests, compatibility lanes,
transport results, residual risks, and independent review.

Until that provider-backed assessment exists and the upstream acceptance standard
is itself green, the adoption decision remains **request changes / not certified**.
This does not weaken the repository's normal CI gates: the pinned canonical
capability-manifest schema and AFDS validator are executed directly in CI.

## Known residual risk

The new runtime policy surface and context runtime modules are strict-mypy gates.
The older monolithic context analyzers/formatters/utilities and `ha_graph` still
contain pre-existing typing debt under a narrowly scoped override. That debt is
not represented as complete L3 compliance and must be removed before any future
assessment claims full strict typing of the entire repository.
