# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.2.x   | :white_check_mark: |

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

- `HA_TOKEN` is never exposed in tool outputs
- Credentials are redacted from all log output
- Environment variables are never logged

### Filesystem Restrictions

- Access is limited to the `/config` directory (Home Assistant configuration)
- Path traversal attempts (e.g., `../etc/passwd`) are blocked
- Maximum file size: 10MB
- Maximum directory depth: 20 levels
- Auth files (`auth`, `auth_provider.*`) are explicitly blocked

### Network

- Only outbound HTTP/HTTPS connections to the configured `HA_URL`
- No inbound connections other than the exposed API ports

## Dependencies

We monitor dependencies for known vulnerabilities. To check for vulnerabilities in your installation:

```bash
pip install safety
safety check -r requirements.txt
```
