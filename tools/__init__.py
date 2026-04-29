"""
Home Assistant MCP Tools Package

This package contains all MCP tools for Home Assistant management.
Tools are organized by functionality:

Core Tools (Always enabled):
- states: Entity state management
- automations: Automation analysis and diagnostics
- scripts: Script management
- scenes: Scene management
- blueprints: Blueprint management
- config: Configuration file tools
- logs: Log analysis
- storage: Registry and storage access
- diagnostics: System diagnostics
- health_reporter: Health reporting

System Tools (Host access):
- system_explorer: File system exploration
- docker_observer: Docker container management
- journal_explorer: Systemd journal access

Dev Tools (Optional):
- dev_tools: Template testing, validation, debugging

New Tools (Phase 1-4):
- config_entries: Config entry management (P0)
- devices: Device details and context (P0)
- entity_dependencies: Entity usage tracking (P1)
- history: History analysis and summaries (P1)
- areas: Area device summaries (P2)
- integrations: Integration entity analysis (P3)

Shared Utilities:
- utils: HTTP client, registry loader, log sanitization
- yaml_utils: Custom YAML loader for HA tags
"""

__version__ = "1.0.0"
