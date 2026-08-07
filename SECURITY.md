---
description: Supported versions, vulnerability reporting, and security boundaries for HA-MCP-Readonly.
doc_id: reference.ha-mcp-security-policy
type: reference
status: active
rigor: normative
owners: [repository-maintainers]
verification: Run the security-boundary unit tests, Bandit, authenticated runtime checks, and context redaction tests.
---

# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.6.x   | Supported |

## Reporting a Vulnerability

If you discover a security vulnerability in HA-MCP-Readonly, please report it responsibly.

**Do NOT open a public GitHub issue for security vulnerabilities.**

Instead, please send an email to the maintainers with:
- A description of the vulnerability
- Steps to reproduce (if applicable)
- Potential impact assessment
- Any suggested fixes

We aim to respond to security reports within 72 hours and will work with you to verify, address, and disclose the issue appropriately.

## Security Design

### Read-Only Architecture

This project is intentionally read-only. It **cannot**:
- Modify entity states
- Execute automations or scripts
- Change device configurations
- Write to the Home Assistant filesystem

### Token Handling

- `HA_TOKEN` and caller bearer tokens are never exposed in tool outputs.
- Context snapshots recursively redact credential-bearing fields, bearer tokens, JWTs, and secret query values.
- Credential stores and secrets files are excluded from context content.
- Environment variables are never logged.

### Filesystem Restrictions

- Generic tools are limited to configured roots and reject traversal, sibling-prefix, and symlink escapes.
- Generic reads block `.storage`, secrets files, environment files, and authentication records.
- The context generator uses a separate reviewed collector, records provenance, applies source and output limits, and publishes atomically below `CONTEXT_OUTPUT_ROOT`.
- Auth files (`auth`, `auth_provider.*`, `onboarding`, cloud credentials, and UUID records) are explicitly blocked from context content.

### Network

- Outbound Home Assistant traffic is limited to the configured `HA_URL`; offline context mode creates no network client.
- Stdio is the default MCP transport. Network MCP and optional REST require bearer authentication.
- Health, MCP, and REST ports should remain loopback-bound unless a trusted reverse proxy and network policy are in place.

### Release trust boundary

- Candidate source is built and exercised before the protected publication step.
- Release images are pushed first to an isolated quarantine repository and identified by an exact registry digest.
- The protected publisher does not check out, build, load, or run candidate source or images; it only promotes the already tested digest and verifies that the promoted digest is identical.
- Every published architecture (`linux/amd64`, `linux/arm64`) is smoke-tested from the exact quarantined manifest before promotion.
- Quarantine write credentials and protected release credentials must be distinct and scoped to their respective repositories/environments.

## Dependencies

We monitor dependencies for known vulnerabilities. To check for vulnerabilities in your installation:

```bash
pip install pip-audit
pip-audit
```
