"""Context-local runtime state for isolated generation runs."""

from __future__ import annotations

import contextlib
import contextvars
from collections.abc import Iterator
from dataclasses import dataclass

from .config import GenerationConfig
from .provenance import ProvenanceTracker


@dataclass(frozen=True, slots=True)
class GenerationRuntime:
    config: GenerationConfig
    provenance: ProvenanceTracker


_current_runtime: contextvars.ContextVar[GenerationRuntime | None] = contextvars.ContextVar(
    "ha_context_runtime", default=None
)


@contextlib.contextmanager
def generation_scope(runtime: GenerationRuntime) -> Iterator[None]:
    token = _current_runtime.set(runtime)
    try:
        yield
    finally:
        _current_runtime.reset(token)


def current_runtime() -> GenerationRuntime | None:
    return _current_runtime.get()


def current_config() -> GenerationConfig | None:
    runtime = current_runtime()
    return runtime.config if runtime else None


def current_provenance() -> ProvenanceTracker | None:
    runtime = current_runtime()
    return runtime.provenance if runtime else None
