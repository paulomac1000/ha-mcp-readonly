"""Home Assistant Context Generator for AI.

Splits the monolithic context_generator.py into focused modules:
- constants: configuration, regex patterns, and YAML loader
- utils: helper functions for registry loading, entity extraction, etc.
- analyzers: data collection and analysis classes
- formatters: Markdown report generation
- core: entry points (main, generate_context_file)
"""

from .core import generate_context_file, main

__all__ = ["generate_context_file", "main"]
