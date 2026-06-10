"""
Tests for tools/utils.py
"""

import json
from unittest.mock import MagicMock, Mock, patch

import pytest


class TestMakeHaRequest:
    """Tests for make_ha_request function."""

    @patch("tools.utils.requests")
    def test_successful_get_request(self, mock_requests):
        from tools.utils import make_ha_request

        mock_response = Mock()
        mock_response.json.return_value = {"test": "data"}
        mock_response.raise_for_status = Mock()
        mock_requests.get.return_value = mock_response

        result = make_ha_request("http://localhost:8123", "test_token", "/api/states")

        assert result["success"] is True
        assert result["data"] == {"test": "data"}

    @patch("tools.utils.requests")
    def test_successful_post_request(self, mock_requests):
        from tools.utils import make_ha_request

        mock_response = Mock()
        mock_response.json.return_value = {"state": "on"}
        mock_response.raise_for_status = Mock()
        mock_requests.post.return_value = mock_response

        result = make_ha_request(
            "http://localhost:8123",
            "test_token",
            "/api/states/light.test",
            method="POST",
            data={"state": "on"},
        )

        assert result["success"] is True
        mock_requests.post.assert_called_once()
        mock_requests.get.assert_not_called()

    @patch("tools.utils.requests")
    def test_non_json_response_returns_text(self, mock_requests):
        from tools.utils import make_ha_request

        mock_response = Mock()
        mock_response.json.side_effect = ValueError("not JSON")
        mock_response.text = "OK"
        mock_response.raise_for_status = Mock()
        mock_requests.get.return_value = mock_response

        result = make_ha_request("http://localhost:8123", "tok", "/api/ping")

        assert result["success"] is True
        assert result["data"] == "OK"

    @patch("tools.utils.requests")
    def test_failed_request_with_retry(self, mock_requests):
        import requests

        from tools.utils import make_ha_request

        mock_requests.exceptions = requests.exceptions
        mock_requests.get.side_effect = requests.exceptions.ConnectionError("Test error")

        result = make_ha_request(
            "http://localhost:8123",
            "test_token",
            "/api/states",
            retries=2,
            backoff=0.01,
        )

        assert result["success"] is False
        assert "Test error" in result["error"]
        assert mock_requests.get.call_count == 2


class TestRegistryLoading:
    """Tests for registry loading functions."""

    def test_load_registry_success(self):
        from tools.utils import invalidate_registry_cache, load_registry

        invalidate_registry_cache()

        with patch("tools.utils.Path") as mock_path_class:
            mock_path = MagicMock()
            mock_path_class.return_value = mock_path
            mock_path.__truediv__ = MagicMock(return_value=mock_path)
            mock_path.exists.return_value = True

            with patch("builtins.open", create=True) as mock_open:
                mock_file = MagicMock()
                mock_file.__enter__ = MagicMock(return_value=mock_file)
                mock_file.__exit__ = MagicMock(return_value=False)
                mock_open.return_value = mock_file

                with patch("json.load", return_value={"data": {"entities": []}}):
                    result = load_registry("core.entity_registry", "/config", use_cache=False)

        assert result == {"data": {"entities": []}}

    def test_load_registry_file_not_found(self):
        from tools.utils import invalidate_registry_cache, load_registry

        invalidate_registry_cache()

        with patch("tools.utils.Path") as mock_path_class:
            mock_path = MagicMock()
            mock_path_class.return_value = mock_path
            mock_path.__truediv__ = MagicMock(return_value=mock_path)
            mock_path.exists.return_value = False

            result = load_registry("nonexistent", "/config", use_cache=False)

        assert result == {}

    def test_load_registry_blocked_returns_empty(self):
        """Blocked registries (auth, onboarding) must silently return {}."""
        from tools.utils import BLOCKED_REGISTRIES, load_registry

        for blocked in BLOCKED_REGISTRIES:
            result = load_registry(blocked, "/config")
            assert result == {}, f"Expected {{}} for blocked registry '{blocked}'"

    def test_load_registry_cache_hit(self):
        """Second call with same key should return cached data without re-reading."""
        from tools.utils import invalidate_registry_cache, load_registry

        invalidate_registry_cache()

        payload = {"data": {"entities": [{"entity_id": "sensor.x"}]}}

        with patch("tools.utils.Path") as mock_path_class:
            mock_path = MagicMock()
            mock_path_class.return_value = mock_path
            mock_path.__truediv__ = MagicMock(return_value=mock_path)
            mock_path.exists.return_value = True
            with patch("builtins.open", create=True) as mock_open_fn:
                mock_file = MagicMock()
                mock_file.__enter__ = MagicMock(return_value=mock_file)
                mock_file.__exit__ = MagicMock(return_value=False)
                mock_open_fn.return_value = mock_file
                with patch("json.load", return_value=payload):
                    load_registry("core.entity_registry", "/config", use_cache=True)
                    result2 = load_registry("core.entity_registry", "/config", use_cache=True)
                    assert mock_open_fn.call_count == 1  # file opened only once
        assert result2 == payload

    def test_invalidate_registry_cache_selective(self):
        """invalidate_registry_cache(registry_name=...) removes only matching keys."""
        from tools.utils import (
            _REGISTRY_CACHE,
            invalidate_registry_cache,
            load_registry,
        )

        invalidate_registry_cache()

        payload = {"data": {}}
        with patch("tools.utils.Path") as mock_path_class:
            mock_path = MagicMock()
            mock_path_class.return_value = mock_path
            mock_path.__truediv__ = MagicMock(return_value=mock_path)
            mock_path.exists.return_value = True
            with patch("builtins.open", create=True) as mock_open_fn:
                mock_file = MagicMock()
                mock_file.__enter__ = MagicMock(return_value=mock_file)
                mock_file.__exit__ = MagicMock(return_value=False)
                mock_open_fn.return_value = mock_file
                with patch("json.load", return_value=payload):
                    load_registry("core.entity_registry", "/config")
                    load_registry("core.device_registry", "/config")

        invalidate_registry_cache(registry_name="core.entity_registry")
        remaining = list(_REGISTRY_CACHE.keys())
        assert not any("core.entity_registry" in k for k in remaining)
        assert any("core.device_registry" in k for k in remaining)


class TestHelperFunctions:
    """Tests for helper functions."""

    def test_get_best_name_entity(self):
        from tools.utils import get_best_name

        entity = {
            "name": "Custom Name",
            "original_name": "Original",
            "entity_id": "sensor.test",
        }
        assert get_best_name(entity, "entity") == "Custom Name"

        entity = {"original_name": "Original", "entity_id": "sensor.test"}
        assert get_best_name(entity, "entity") == "Original"

        entity = {"entity_id": "sensor.test"}
        assert get_best_name(entity, "entity") == "sensor.test"

    def test_get_best_name_device(self):
        from tools.utils import get_best_name

        device = {"name_by_user": "User Name", "name": "Default"}
        assert get_best_name(device, "device") == "User Name"

        device = {"name": "Default"}
        assert get_best_name(device, "device") == "Default"

    def test_resolve_area_id_from_entity(self):
        from tools.utils import resolve_area_id

        entity = {"area_id": "living_room", "device_id": "dev1"}
        device_map = {"dev1": {"area_id": "bedroom"}}

        assert resolve_area_id(entity, device_map) == "living_room"

    def test_resolve_area_id_from_device(self):
        from tools.utils import resolve_area_id

        entity = {"device_id": "dev1"}
        device_map = {"dev1": {"area_id": "bedroom"}}

        assert resolve_area_id(entity, device_map) == "bedroom"

    def test_sanitize_for_json(self):
        from tools.utils import sanitize_for_json

        data = {
            "username": "admin",
            "password": "secret123",
            "api_key": "key123",
            "settings": {"token": "tok123", "enabled": True},
        }

        result = sanitize_for_json(data)

        assert result["username"] == "admin"
        assert result["password"] == "***REDACTED***"
        assert result["api_key"] == "***REDACTED***"
        assert result["settings"]["token"] == "***REDACTED***"
        assert result["settings"]["enabled"] is True

    def test_sanitize_for_json_list(self):
        from tools.utils import sanitize_for_json

        items = [{"password": "x"}, {"name": "safe"}]
        result = sanitize_for_json(items)
        assert result[0]["password"] == "***REDACTED***"
        assert result[1]["name"] == "safe"

    def test_resolve_area_id_none_when_missing(self):
        from tools.utils import resolve_area_id

        entity = {"device_id": "dev_unknown"}
        assert resolve_area_id(entity, {}) is None

        entity_no_device = {}
        assert resolve_area_id(entity_no_device, {}) is None


class TestSanitizeLogLine:
    """Tests for sanitize_log_line security function."""

    def test_redacts_bearer_token(self):
        from tools.utils import sanitize_log_line

        line = "Authorization: Bearer eyABCDEFGHIJ.token.value"
        result = sanitize_log_line(line)
        assert "Bearer [REDACTED]" in result
        assert "eyABCDEFGHIJ" not in result

    def test_redacts_password(self):
        from tools.utils import sanitize_log_line

        assert "[REDACTED]" in sanitize_log_line("password=supersecret")
        assert "[REDACTED]" in sanitize_log_line("passwd: topsecret")

    def test_redacts_token(self):
        from tools.utils import sanitize_log_line

        assert "[REDACTED]" in sanitize_log_line("token=abc123xyz")
        assert "[REDACTED]" in sanitize_log_line("access_token=xyz")

    def test_redacts_api_key(self):
        from tools.utils import sanitize_log_line

        assert "[REDACTED]" in sanitize_log_line("api_key=mykey123")

    def test_redacts_ip_address(self):
        from tools.utils import sanitize_log_line

        result = sanitize_log_line("Connected from 192.168.1.100")
        assert "192.168.1.100" not in result
        assert "[IP_REDACTED]" in result

    def test_safe_line_unchanged(self):
        from tools.utils import sanitize_log_line

        line = "INFO Home Assistant started successfully"
        assert sanitize_log_line(line) == line


class TestGetRegistryCacheStats:
    """Tests for get_registry_cache_stats observability function."""

    def test_stats_structure(self):
        from tools.utils import get_registry_cache_stats, invalidate_registry_cache

        invalidate_registry_cache()
        stats = get_registry_cache_stats()
        for key in (
            "hits",
            "misses",
            "blocked",
            "total",
            "hit_rate_percent",
            "cached_keys",
        ):
            assert key in stats

    def test_hit_rate_zero_when_no_requests(self):
        from tools.utils import _REGISTRY_CACHE_STATS, get_registry_cache_stats

        _REGISTRY_CACHE_STATS["hits"] = 0
        _REGISTRY_CACHE_STATS["misses"] = 0
        stats = get_registry_cache_stats()
        assert stats["hit_rate_percent"] == 0.0


class TestLoadRegistryErrors:
    """Tests for error paths in load_registry."""

    def test_load_registry_json_decode_error(self):
        """JSONDecodeError should return {}."""
        from tools.utils import invalidate_registry_cache, load_registry

        invalidate_registry_cache()

        with patch("tools.utils.Path") as mock_path_class:
            mock_path = MagicMock()
            mock_path_class.return_value = mock_path
            mock_path.__truediv__ = MagicMock(return_value=mock_path)
            mock_path.exists.return_value = True

            with patch("builtins.open", create=True) as mock_open_fn:
                mock_file = MagicMock()
                mock_open_fn.return_value = mock_file
                mock_file.__enter__.return_value = mock_file
                mock_file.__exit__.return_value = False

                import json as json_module

                with patch("json.load") as mock_json_load:
                    mock_json_load.side_effect = json_module.JSONDecodeError("bad json", "{}", 0)
                    result = load_registry("core.entity_registry", "/config", use_cache=False)

        assert result == {}

    def test_load_registry_io_error(self):
        """IOError during file open should return {}."""
        from tools.utils import invalidate_registry_cache, load_registry

        invalidate_registry_cache()

        with patch("tools.utils.Path") as mock_path_class:
            mock_path = MagicMock()
            mock_path_class.return_value = mock_path
            mock_path.__truediv__ = MagicMock(return_value=mock_path)
            mock_path.exists.return_value = True
            with patch("builtins.open", side_effect=OSError("permission denied")):
                result = load_registry("core.entity_registry", "/config", use_cache=False)

        assert result == {}

    def test_invalidate_cache_by_config_path(self):
        """invalidate_registry_cache(config_path=...) removes matching keys."""
        from tools.utils import (
            _REGISTRY_CACHE,
            invalidate_registry_cache,
        )

        # Pre-populate cache
        _REGISTRY_CACHE["/config1/core.entity_registry"] = ({}, 0)
        _REGISTRY_CACHE["/config1/core.device_registry"] = ({}, 0)
        _REGISTRY_CACHE["/config2/core.entity_registry"] = ({}, 0)

        invalidate_registry_cache(config_path="/config1")
        remaining = list(_REGISTRY_CACHE.keys())
        assert "/config1/core.entity_registry" not in remaining
        assert "/config1/core.device_registry" not in remaining
        assert "/config2/core.entity_registry" in remaining

    def test_resolve_area_id_from_device_unknown(self):
        """resolve_area_id returns None when device not found in map."""
        from tools.utils import resolve_area_id

        entity = {"device_id": "nonexistent_dev"}
        device_map = {"other_dev": {"area_id": "kitchen"}}
        assert resolve_area_id(entity, device_map) is None


class TestTailLogFile:
    """Tests for tail_log_file utility."""

    def test_returns_lines(self, tmp_path):
        from tools.utils import tail_log_file

        log = tmp_path / "test.log"
        log.write_text("line1\nline2\nline3\n")
        mock_result = Mock(stdout="line2\nline3")
        with patch("tools.utils.subprocess.run", return_value=mock_result):
            lines = tail_log_file(str(log), lines=2)
        assert lines == ["line2", "line3"]

    def test_returns_empty_when_file_missing(self, tmp_path):
        from tools.utils import tail_log_file

        lines = tail_log_file(str(tmp_path / "nonexistent.log"), lines=10)
        assert lines == []

    def test_returns_empty_on_subprocess_error(self, tmp_path):
        import subprocess

        from tools.utils import tail_log_file

        log = tmp_path / "test.log"
        log.write_text("data")
        with patch("tools.utils.subprocess.run", side_effect=subprocess.SubprocessError("fail")):
            lines = tail_log_file(str(log), lines=5)
        assert lines == []


class TestErrorResponseExtended:
    """Tests for the extended (L2+) error contract helpers."""

    def test_error_dict_extended_minimal(self):
        from tools.utils import _error_dict_extended

        d = _error_dict_extended("TIMEOUT", "timed out", True)
        assert d["success"] is False
        assert d["error"]["code"] == "TIMEOUT"
        assert d["error"]["message"] == "timed out"
        assert d["error"]["retryable"] is True
        assert "suggestion" not in d["error"]
        assert "available_names" not in d["error"]

    def test_error_dict_extended_full(self):
        from tools.utils import _error_dict_extended

        d = _error_dict_extended(
            "INVALID_PARAM",
            "bad parameter",
            False,
            suggestion="fix it",
            available_names=["a", "b"],
        )
        assert d["error"]["suggestion"] == "fix it"
        assert d["error"]["available_names"] == ["a", "b"]

    def test_available_names_capped_at_50(self):
        from tools.utils import _error_dict_extended

        d = _error_dict_extended("X", "m", True, available_names=[str(i) for i in range(100)])
        assert len(d["error"]["available_names"]) == 50

    def test_error_response_extended_serializes(self):
        from tools.utils import _error_response_extended

        raw = _error_response_extended("HTTP_ERROR", "boom", True)
        parsed = json.loads(raw)
        assert parsed["success"] is False
        assert parsed["error"]["code"] == "HTTP_ERROR"
        assert parsed["error"]["retryable"] is True


class TestMakeHaRequestErrorCode:
    """Tests for the structured error siblings of make_ha_request."""

    @patch("tools.utils.requests")
    def test_connection_error_code(self, mock_requests):
        import requests

        from tools.utils import make_ha_request

        mock_requests.exceptions = requests.exceptions
        mock_requests.get.side_effect = requests.exceptions.ConnectionError("down")
        result = make_ha_request("http://h", "t", "/api/states", retries=1, backoff=0.01)
        assert result["success"] is False
        assert result["error_code"] == "HTTP_ERROR"
        assert result["retryable"] is True

    @patch("tools.utils.requests")
    def test_timeout_error_code(self, mock_requests):
        import requests

        from tools.utils import make_ha_request

        mock_requests.exceptions = requests.exceptions
        mock_requests.get.side_effect = requests.exceptions.Timeout("slow")
        result = make_ha_request("http://h", "t", "/api/states", retries=1, backoff=0.01)
        assert result["error_code"] == "TIMEOUT"
        assert result["retryable"] is True


class TestSanitizeResponseData:
    """Tests for the recursive response data sanitizer."""

    def test_sanitizes_jwt_in_string(self):
        from tools.utils import sanitize_response_data

        result = sanitize_response_data(
            "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dummy"
        )
        assert "eyJhbGciOi" not in str(result)
        assert "JWT_REDACTED" in str(result)

    def test_sanitizes_bearer_token_in_string(self):
        from tools.utils import sanitize_response_data

        result = sanitize_response_data("Authorization: Bearer abcdef123456")
        assert "abcdef123456" not in str(result)
        assert "[REDACTED]" in str(result)

    def test_sanitizes_token_value_in_string(self):
        from tools.utils import sanitize_response_data

        result = sanitize_response_data("token=my-secret-key")
        assert "my-secret-key" not in str(result)
        assert "REDACTED" in str(result)

    def test_sanitizes_nested_dict_values(self):
        from tools.utils import sanitize_response_data

        data = {"logs": ["token=abc123", {"msg": "Bearer xyz789"}]}
        result = sanitize_response_data(data)
        assert "abc123" not in str(result["logs"][0])
        assert "xyz789" not in str(result["logs"][1]["msg"])

    def test_sanitizes_ip_in_string(self):
        from tools.utils import sanitize_response_data

        result = sanitize_response_data("Host: 192.168.1.100 connected")
        assert "192.168.1.100" not in str(result)
        assert "IP_REDACTED" in str(result)

    def test_preserves_non_string_types(self):
        from tools.utils import sanitize_response_data

        assert sanitize_response_data(42) == 42
        assert sanitize_response_data(True) is True
        assert sanitize_response_data(None) is None
        assert sanitize_response_data(3.14) == 3.14

    def test_sanitizes_password_in_string(self):
        from tools.utils import sanitize_response_data

        result = sanitize_response_data("password=supersecret")
        assert "supersecret" not in str(result)
        assert "REDACTED" in str(result)


class TestBuildHistoryUrl:
    """Tests for _build_history_url helper."""

    def test_with_entity_id_and_minimal(self):
        from datetime import UTC, datetime

        from tools.utils import _build_history_url

        dt = datetime(2025, 6, 10, 12, 30, 0, tzinfo=UTC)
        url = _build_history_url(dt, entity_id="light.living_room")
        assert url == (
            "/api/history/period/2025-06-10T12:30:00+00:00"
            "?filter_entity_id=light.living_room"
            "&minimal_response=true"
        )
        # No URL-encoding artifacts from urllib.parse.quote
        assert "%3A" not in url
        assert "%2B" not in url
        assert "%2E" not in url

    def test_without_entity_id_not_minimal(self):
        from datetime import UTC, datetime

        from tools.utils import _build_history_url

        dt = datetime(2025, 6, 10, 12, 30, 0, tzinfo=UTC)
        url = _build_history_url(dt, minimal=False)
        assert url == ("/api/history/period/2025-06-10T12:30:00+00:00?minimal_response=false")
        assert "%3A" not in url

    def test_with_entity_id_not_minimal(self):
        from datetime import UTC, datetime

        from tools.utils import _build_history_url

        dt = datetime(2025, 6, 10, 12, 30, 0, tzinfo=UTC)
        url = _build_history_url(dt, entity_id="sensor.temp", minimal=False)
        assert url == (
            "/api/history/period/2025-06-10T12:30:00+00:00"
            "?filter_entity_id=sensor.temp"
            "&minimal_response=false"
        )

    def test_with_comma_separated_entity_ids(self):
        from datetime import UTC, datetime

        from tools.utils import _build_history_url

        dt = datetime(2025, 6, 10, 12, 30, 0, tzinfo=UTC)
        url = _build_history_url(dt, entity_id="light.a,light.b")
        assert "filter_entity_id=light.a,light.b" in url
        assert "%2C" not in url


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
