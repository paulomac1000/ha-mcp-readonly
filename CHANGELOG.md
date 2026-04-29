# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2025-04-29

### Added
- Initial release of HA-MCP-Readonly
- 110+ read-only MCP tools for Home Assistant observation
- REST API on port 9093 with OpenAPI schema
- MCP SSE transport on port 9092 for AI clients
- Health check endpoint on port 9091
- Context generator with offline/online/hybrid modes
- Docker and Docker Compose support
- Comprehensive unit test suite (411 tests)
- Integration tests for real Home Assistant instances
- Filesystem access restricted to `/config` directory
- Credential redaction in logs and outputs

### Security
- Read-only design: no write operations to Home Assistant
- Auth registry blocked from AI access
- Path traversal protection
- Max file size and directory depth limits
