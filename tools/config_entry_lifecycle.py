"""
Config Entry Lifecycle Analysis

Provides lifecycle classification for Home Assistant config entries:
- ConfigEntryLifecycle frozen dataclass (state, reason, api_state, disabled_by)
- fetch_entry_states(ha_url, ha_token) -> entry_id to API state mapping
- classify_entry_lifecycle(entry, api_states, entity_count) -> ConfigEntryLifecycle
- classify_domain_lifecycle(lifecycles) -> domain-level state/reason/per_entry summary
"""

import logging
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from tools.utils import make_ha_request

_logger = logging.getLogger(__name__)

TOOLS_VERSION = "1.0.0"

# Normalized lifecycle states for a single config entry.
ACTIVE = "ACTIVE"
LOADED_INERT = "LOADED_INERT"
CONFIGURED_NOT_LOADED = "CONFIGURED_NOT_LOADED"
UNKNOWN = "UNKNOWN"

# Domain-level aggregate state when entries disagree.
PARTIAL = "PARTIAL"

# Only REST API state meaning the entry is actually loaded.
LOADED_API_STATE = "loaded"

# REST API states meaning the entry is configured but not loaded.
_NOT_LOADED_API_STATES = frozenset(
    {
        "setup_retry",
        "setup_error",
        "failed",
        "not_loaded",
        "migration_error",
        "failed_unload",
        "setup_in_progress",
        "unload_in_progress",
    }
)


@dataclass(frozen=True)
class ConfigEntryLifecycle:
    """Lifecycle classification of a single Home Assistant config entry.

    Attributes:
        state: Normalized lifecycle state (ACTIVE, LOADED_INERT, CONFIGURED_NOT_LOADED, UNKNOWN).
        reason: Human-readable explanation for the classification.
        api_state: Raw state reported by the config entry REST API, or None when unknown.
        disabled_by: Raw disabled_by value from the entry, or None when enabled.
    """

    state: str
    reason: str
    api_state: str | None
    disabled_by: str | None


class HARequestFn(Protocol):
    def __call__(
        self, ha_url: str | None, ha_token: str | None, endpoint: str, *, timeout: int = 10
    ) -> dict[str, Any]: ...


def fetch_entry_states(
    ha_url: str | None, ha_token: str | None, *, request_fn: HARequestFn | None = None
) -> dict[str, str] | None:
    """Fetch real per-entry states from the Home Assistant config entry API.

    ``GET /api/config/config_entries/entry`` is the only source of true entry
    states; ``.storage/core.config_entries`` carries no ``state`` field at all.

    Args:
        ha_url: Home Assistant base URL, or None when unconfigured.
        ha_token: Long-lived access token, or None when unconfigured.

    Returns:
        Mapping of entry_id to API state, or None when the API is unreachable
        so callers can distinguish "no state available" from a real state.
    """
    result = (request_fn or make_ha_request)(
        ha_url, ha_token, "/api/config/config_entries/entry", timeout=30
    )
    if not result.get("success") or not isinstance(result.get("data"), list):
        return None
    states: dict[str, str] = {}
    for entry in result["data"]:
        entry_id = entry.get("entry_id")
        state = entry.get("state")
        if isinstance(entry_id, str) and isinstance(state, str):
            states[entry_id] = state
    return states


def classify_entry_lifecycle(
    entry: dict[str, Any],
    api_states: dict[str, str] | None,
    entity_count: int | None,
) -> ConfigEntryLifecycle:
    """Classify the lifecycle of a single config entry from its API state.

    Args:
        entry: Config entry record with entry_id and an optional disabled_by value.
        api_states: Mapping of entry_id to API state, or None when the API is unavailable.
        entity_count: Number of entities provided by the entry, or None when
            the caller intentionally skipped registry lookup.

    Returns:
        A frozen ConfigEntryLifecycle describing the entry's lifecycle.
    """
    disabled_by = entry.get("disabled_by")
    entry_id = entry.get("entry_id")
    api_state = api_states.get(entry_id) if api_states is not None and entry_id else None

    if disabled_by:
        return ConfigEntryLifecycle(
            state=CONFIGURED_NOT_LOADED,
            reason=f"Disabled by {disabled_by}",
            api_state=api_state,
            disabled_by=disabled_by,
        )

    if api_states is None:
        return ConfigEntryLifecycle(
            state=UNKNOWN,
            reason="Config entry API is unavailable",
            api_state=None,
            disabled_by=None,
        )

    if not entry_id:
        return ConfigEntryLifecycle(
            state=UNKNOWN,
            reason="Entry has no entry_id",
            api_state=None,
            disabled_by=None,
        )

    if api_state is None:
        return ConfigEntryLifecycle(
            state=UNKNOWN,
            reason=f"Entry {entry_id} is not reported by the config entry API",
            api_state=None,
            disabled_by=None,
        )

    if api_state == LOADED_API_STATE:
        if entity_count is None:
            return ConfigEntryLifecycle(
                state=UNKNOWN,
                reason="Entry is loaded but its entity count was not observed",
                api_state=api_state,
                disabled_by=None,
            )
        if entity_count == 0:
            return ConfigEntryLifecycle(
                state=LOADED_INERT,
                reason="Entry is loaded but provides no entities",
                api_state=api_state,
                disabled_by=None,
            )
        return ConfigEntryLifecycle(
            state=ACTIVE,
            reason=f"Entry is loaded and provides {entity_count} entities",
            api_state=api_state,
            disabled_by=None,
        )

    if api_state in _NOT_LOADED_API_STATES:
        return ConfigEntryLifecycle(
            state=CONFIGURED_NOT_LOADED,
            reason=f"Entry state is {api_state}; configured but not loaded",
            api_state=api_state,
            disabled_by=None,
        )

    return ConfigEntryLifecycle(
        state=UNKNOWN,
        reason=f"Unrecognized config entry state: {api_state}",
        api_state=api_state,
        disabled_by=None,
    )


def classify_domain_lifecycle(lifecycles: dict[str, ConfigEntryLifecycle]) -> dict[str, Any]:
    """Aggregate per-entry lifecycles into a domain-level summary.

    Args:
        lifecycles: Mapping of entry_id to its ConfigEntryLifecycle classification.

    Returns:
        Dict with state, reason, and per_entry details. State is PARTIAL when
        entries disagree and UNKNOWN when there are no entries.
    """
    per_entry = {entry_id: asdict(lifecycle) for entry_id, lifecycle in lifecycles.items()}

    if not lifecycles:
        return {
            "state": UNKNOWN,
            "reason": "No config entries for this domain",
            "per_entry": per_entry,
        }

    states = {lifecycle.state for lifecycle in lifecycles.values()}

    if len(states) == 1:
        state = states.pop()
        reason = f"All {len(lifecycles)} entries are {state}"
    else:
        state = PARTIAL
        reason = f"Entries disagree: {', '.join(sorted(states))}"

    return {"state": state, "reason": reason, "per_entry": per_entry}
