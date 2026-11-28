"""Tests for tools/config_entry_lifecycle.py

Covers per-entry lifecycle classification branches, REST API state fetching
via monkeypatched make_ha_request, and domain-level aggregation.
"""

from dataclasses import FrozenInstanceError

import pytest

from tools.config_entry_lifecycle import (
    ACTIVE,
    CONFIGURED_NOT_LOADED,
    LOADED_INERT,
    PARTIAL,
    UNKNOWN,
    ConfigEntryLifecycle,
    classify_domain_lifecycle,
    classify_entry_lifecycle,
    fetch_entry_states,
)


class TestFetchEntryStates:
    """Tests for fetch_entry_states()."""

    def test_returns_entry_state_map_when_api_succeeds(self, monkeypatch: pytest.MonkeyPatch):
        """A successful API response maps entry_id to its raw state."""
        monkeypatch.setattr(
            "tools.config_entry_lifecycle.make_ha_request",
            lambda *args, **kwargs: {
                "success": True,
                "data": [
                    {"entry_id": "entry_mqtt", "state": "loaded"},
                    {"entry_id": "entry_hue", "state": "setup_retry"},
                ],
            },
        )

        assert fetch_entry_states("http://localhost:8123", "token") == {
            "entry_mqtt": "loaded",
            "entry_hue": "setup_retry",
        }

    def test_returns_none_when_api_fails(self, monkeypatch: pytest.MonkeyPatch):
        """An unsuccessful API response yields None, not a partial mapping."""
        monkeypatch.setattr(
            "tools.config_entry_lifecycle.make_ha_request",
            lambda *args, **kwargs: {"success": False, "error": "connection refused"},
        )

        assert fetch_entry_states("http://localhost:8123", "token") is None

    def test_returns_none_when_data_is_not_a_list(self, monkeypatch: pytest.MonkeyPatch):
        """A success envelope whose data is not a list yields None."""
        monkeypatch.setattr(
            "tools.config_entry_lifecycle.make_ha_request",
            lambda *args, **kwargs: {"success": True, "data": {"unexpected": True}},
        )

        assert fetch_entry_states("http://localhost:8123", "token") is None

    def test_skips_malformed_records(self, monkeypatch: pytest.MonkeyPatch):
        """Records lacking string entry_id or state are dropped from the mapping."""
        monkeypatch.setattr(
            "tools.config_entry_lifecycle.make_ha_request",
            lambda *args, **kwargs: {
                "success": True,
                "data": [
                    {"entry_id": "entry_ok", "state": "loaded"},
                    {"entry_id": None, "state": "loaded"},
                    {"entry_id": "entry_nostate"},
                ],
            },
        )

        assert fetch_entry_states("http://localhost:8123", "token") == {"entry_ok": "loaded"}

    def test_calls_config_entry_endpoint(self, monkeypatch: pytest.MonkeyPatch):
        """The request targets the config entry REST endpoint with a raised timeout."""
        calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

        def fake_request(*args: object, **kwargs: object) -> dict[str, object]:
            calls.append((args, kwargs))
            return {"success": True, "data": []}

        monkeypatch.setattr("tools.config_entry_lifecycle.make_ha_request", fake_request)

        fetch_entry_states("http://localhost:8123", "token")

        assert calls[0][0][:3] == (
            "http://localhost:8123",
            "token",
            "/api/config/config_entries/entry",
        )


class TestClassifyEntryLifecycle:
    """Tests for classify_entry_lifecycle() branches."""

    def test_disabled_by_entry_is_configured_not_loaded_even_when_api_reports_loaded(self):
        """disabled_by takes precedence over a loaded REST API state."""
        entry = {"entry_id": "entry_mqtt", "disabled_by": "user"}
        api_states = {"entry_mqtt": "loaded"}

        lifecycle = classify_entry_lifecycle(entry, api_states, entity_count=3)

        assert lifecycle.state == CONFIGURED_NOT_LOADED
        assert lifecycle.disabled_by == "user"
        assert lifecycle.api_state == "loaded"
        assert lifecycle.reason

    def test_missing_api_states_yields_unknown(self):
        """None API states mean the classification cannot be trusted."""
        entry = {"entry_id": "entry_mqtt"}

        lifecycle = classify_entry_lifecycle(entry, None, entity_count=5)

        assert lifecycle.state == UNKNOWN
        assert lifecycle.api_state is None
        assert lifecycle.disabled_by is None
        assert lifecycle.reason

    def test_entry_absent_from_api_states_yields_unknown(self):
        """An entry the API does not report cannot be classified."""
        entry = {"entry_id": "entry_mqtt"}
        api_states = {"entry_hue": "loaded"}

        lifecycle = classify_entry_lifecycle(entry, api_states, entity_count=5)

        assert lifecycle.state == UNKNOWN
        assert lifecycle.api_state is None
        assert lifecycle.reason

    def test_entry_without_entry_id_yields_unknown(self):
        """A record lacking entry_id cannot be matched to any API state."""
        entry = {"disabled_by": None}

        lifecycle = classify_entry_lifecycle(entry, {"entry_hue": "loaded"}, entity_count=1)

        assert lifecycle.state == UNKNOWN
        assert lifecycle.api_state is None
        assert lifecycle.reason

    def test_loaded_entry_with_zero_entities_is_loaded_inert(self):
        """A loaded entry providing no entities is inert, not active."""
        entry = {"entry_id": "entry_mqtt"}
        api_states = {"entry_mqtt": "loaded"}

        lifecycle = classify_entry_lifecycle(entry, api_states, entity_count=0)

        assert lifecycle.state == LOADED_INERT
        assert lifecycle.api_state == "loaded"
        assert lifecycle.disabled_by is None
        assert lifecycle.reason

    def test_loaded_entry_with_entities_is_active(self):
        """A loaded entry providing entities is active."""
        entry = {"entry_id": "entry_mqtt"}
        api_states = {"entry_mqtt": "loaded"}

        lifecycle = classify_entry_lifecycle(entry, api_states, entity_count=4)

        assert lifecycle.state == ACTIVE
        assert lifecycle.api_state == "loaded"
        assert lifecycle.reason

    @pytest.mark.parametrize(
        "api_state",
        [
            "setup_retry",
            "setup_error",
            "failed",
            "not_loaded",
            "migration_error",
            "failed_unload",
            "setup_in_progress",
            "unload_in_progress",
        ],
    )
    def test_not_loaded_api_states_are_configured_not_loaded(self, api_state: str):
        """Every known configured-but-not-loaded API state maps to CONFIGURED_NOT_LOADED."""
        entry = {"entry_id": "entry_mqtt"}
        states = {"entry_mqtt": api_state}

        lifecycle = classify_entry_lifecycle(entry, states, entity_count=2)

        assert lifecycle.state == CONFIGURED_NOT_LOADED
        assert lifecycle.api_state == api_state
        assert lifecycle.disabled_by is None
        assert lifecycle.reason

    def test_unrecognized_api_state_yields_unknown(self):
        """An API state outside the known vocabulary cannot be classified."""
        entry = {"entry_id": "entry_mqtt"}
        states = {"entry_mqtt": "frobnicating"}

        lifecycle = classify_entry_lifecycle(entry, states, entity_count=2)

        assert lifecycle.state == UNKNOWN
        assert lifecycle.api_state == "frobnicating"
        assert lifecycle.reason

    def test_lifecycle_is_frozen(self):
        """The classification value object is immutable."""
        entry = {"entry_id": "entry_mqtt"}
        api_states = {"entry_mqtt": "loaded"}

        lifecycle = classify_entry_lifecycle(entry, api_states, entity_count=1)

        with pytest.raises(FrozenInstanceError):
            setattr(lifecycle, "state", ACTIVE)


class TestClassifyDomainLifecycle:
    """Tests for classify_domain_lifecycle() aggregation."""

    def test_empty_lifecycles_yields_unknown(self):
        """A domain with no entries aggregates to UNKNOWN with empty per_entry."""
        result = classify_domain_lifecycle({})

        assert result["state"] == UNKNOWN
        assert result["per_entry"] == {}
        assert result["reason"]

    def test_single_entry_domain_adopts_entry_state(self):
        """A one-entry domain reports that entry's state."""
        lifecycles = {
            "entry_mqtt": ConfigEntryLifecycle(
                state=ACTIVE, reason="ok", api_state="loaded", disabled_by=None
            )
        }

        result = classify_domain_lifecycle(lifecycles)

        assert result["state"] == ACTIVE
        assert result["reason"]

    def test_uniform_entries_share_their_state(self):
        """Entries agreeing on a state aggregate to that single state."""
        lifecycles = {
            "entry_mqtt": ConfigEntryLifecycle(
                state=ACTIVE, reason="ok", api_state="loaded", disabled_by=None
            ),
            "entry_hue": ConfigEntryLifecycle(
                state=ACTIVE, reason="ok", api_state="loaded", disabled_by=None
            ),
        }

        result = classify_domain_lifecycle(lifecycles)

        assert result["state"] == ACTIVE
        assert result["reason"]

    def test_mixed_entries_yield_partial(self):
        """Entries disagreeing on state aggregate to PARTIAL."""
        lifecycles = {
            "entry_mqtt": ConfigEntryLifecycle(
                state=ACTIVE, reason="ok", api_state="loaded", disabled_by=None
            ),
            "entry_hue": ConfigEntryLifecycle(
                state=CONFIGURED_NOT_LOADED,
                reason="setup_retry",
                api_state="setup_retry",
                disabled_by=None,
            ),
            "entry_zwave": ConfigEntryLifecycle(
                state=UNKNOWN, reason="api unavailable", api_state=None, disabled_by=None
            ),
        }

        result = classify_domain_lifecycle(lifecycles)

        assert result["state"] == PARTIAL
        assert result["reason"]

    def test_per_entry_preserves_lifecycle_fields(self):
        """per_entry serializes every lifecycle field for each entry_id."""
        lifecycle = ConfigEntryLifecycle(
            state=LOADED_INERT, reason="no entities", api_state="loaded", disabled_by=None
        )

        result = classify_domain_lifecycle({"entry_mqtt": lifecycle})

        assert result["per_entry"] == {
            "entry_mqtt": {
                "state": LOADED_INERT,
                "reason": "no entities",
                "api_state": "loaded",
                "disabled_by": None,
            }
        }
