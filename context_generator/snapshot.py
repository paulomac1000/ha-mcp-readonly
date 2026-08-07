"""Bounded, redacted collection of every supported safe Home Assistant data source."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from .config import GenerationConfig
from .provenance import ProvenanceTracker, record_count, redact_sensitive
from .utils import make_ha_request

_logger = logging.getLogger(__name__)

# Credential stores, databases, backups and binary/media trees are deliberately not copied.
_BLOCKED_NAMES = {
    "secrets.yaml",
    "secrets.yml",
    ".env",
    "auth",
    "onboarding",
    "core.restore_state",
}
_BLOCKED_PREFIXES = ("auth_provider.",)
_BLOCKED_DIRS = {"backups", "backup", "media", "www", "tts", "deps", ".git"}
_TEXT_SUFFIXES = {".yaml", ".yml", ".json", ".md", ".txt", ".log", ".jinja", ".j2", ".conf"}

_REST_SOURCES: tuple[tuple[str, str], ...] = (
    ("config_api", "/api/config"),
    ("states_api", "/api/states"),
    ("services_api", "/api/services"),
    ("events_api", "/api/events"),
    ("components_api", "/api/components"),
    ("system_health_api", "/api/system_health"),
    ("energy_dashboard_api", "/api/energy/dashboard"),
    ("error_log_api", "/api/error_log"),
)

_WS_SOURCES: tuple[tuple[str, str, dict[str, Any]], ...] = (
    ("areas_ws", "config/area_registry/list", {}),
    ("devices_ws", "config/device_registry/list", {}),
    ("entities_ws", "config/entity_registry/list", {}),
    ("floors_ws", "config/floor_registry/list", {}),
    ("labels_ws", "config/label_registry/list", {}),
    ("categories_ws", "config/category_registry/list", {"scope": "automation"}),
    ("config_entries_ws", "config_entries/get", {}),
    ("panels_ws", "get_panels", {}),
    ("lovelace_resources_ws", "lovelace/resources", {}),
    ("assist_pipelines_ws", "assist_pipeline/pipeline/list", {}),
    ("energy_preferences_ws", "energy/get_prefs", {}),
    ("repairs_ws", "repairs/list_issues", {}),
    ("system_health_ws", "system_health/info", {}),
)


class ComprehensiveSnapshotCollector:
    """Collect supported runtime and local sources without silently omitting failures."""

    def __init__(self, config: GenerationConfig, provenance: ProvenanceTracker) -> None:
        self.config = config
        self.provenance = provenance
        self.data: dict[str, Any] = {"rest": {}, "websocket": {}, "files": {}}

    @staticmethod
    def _encoded_size(value: Any) -> int:
        try:
            return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))
        except (TypeError, ValueError):
            return len(repr(value).encode("utf-8", errors="replace"))

    def _store(
        self,
        group: str,
        key: str,
        value: Any,
        *,
        method: str,
        status: str = "complete",
        reason: str | None = None,
        requested: str | None = None,
        source_name: str | None = None,
    ) -> Any:
        safe, redactions = redact_sensitive(value)
        size = self._encoded_size(safe)
        if size > self.config.max_source_bytes:
            self.data[group][key] = {"truncated": True, "size_bytes": size}
            self.provenance.record(
                source_name or key,
                method=method,
                status="partial",
                records=0,
                size_bytes=size,
                redacted_fields=redactions,
                reason=f"source exceeds {self.config.max_source_bytes} byte limit",
                requested=requested,
            )
            return self.data[group][key]
        self.data[group][key] = safe
        self.provenance.record(
            source_name or key,
            method=method,
            status=status,  # type: ignore[arg-type]
            records=record_count(safe),
            size_bytes=size,
            redacted_fields=redactions,
            reason=reason,
            requested=requested,
        )
        return safe

    def _unavailable(
        self,
        group: str,
        key: str,
        *,
        method: str,
        reason: str,
        requested: str | None = None,
        status: str = "unavailable",
        source_name: str | None = None,
    ) -> None:
        self.data[group][key] = None
        self.provenance.record(
            source_name or key,
            method=method,
            status=status,  # type: ignore[arg-type]
            reason=reason,
            requested=requested,
        )

    def collect(self) -> dict[str, Any]:
        """Collect local sources always and network sources only when explicitly enabled."""
        self._collect_files()
        if self.config.network_enabled:
            self._collect_rest()
            self._collect_websocket()
        else:
            reason = (
                "offline mode" if self.config.mode == "offline" else "HA credentials unavailable"
            )
            for key, endpoint in _REST_SOURCES:
                self._unavailable(
                    "rest",
                    key,
                    method="rest",
                    reason=reason,
                    requested=endpoint,
                    status="skipped" if self.config.mode == "offline" else "unavailable",
                )
            self._unavailable(
                "rest",
                "calendars",
                method="rest",
                reason=reason,
                requested="/api/calendars",
                status="skipped" if self.config.mode == "offline" else "unavailable",
            )
            for key, command, _ in _WS_SOURCES:
                self._unavailable(
                    "websocket",
                    key,
                    method="websocket",
                    reason=reason,
                    requested=command,
                    status="skipped" if self.config.mode == "offline" else "unavailable",
                )
            for key in ("todo_items", "weather_forecasts"):
                self._unavailable(
                    "websocket",
                    key,
                    method="websocket",
                    reason=reason,
                    status="skipped" if self.config.mode == "offline" else "unavailable",
                )
        return self.data

    def _collect_rest(self) -> None:
        for key, endpoint in _REST_SOURCES:
            result = make_ha_request(endpoint)
            if result.get("success"):
                self._store("rest", key, result.get("data"), method="rest", requested=endpoint)
            else:
                self._unavailable(
                    "rest",
                    key,
                    method="rest",
                    reason=str(result.get("error") or "request failed"),
                    requested=endpoint,
                )

        now = datetime.now(UTC)
        history_start = now - timedelta(hours=self.config.history_hours)
        history_endpoint = "/api/history/period/" + history_start.isoformat()
        history_endpoint += "?" + urlencode(
            {"end_time": now.isoformat(), "minimal_response": "0", "no_attributes": "0"}
        )
        result = make_ha_request(history_endpoint)
        if result.get("success"):
            self._store(
                "rest", "history_api", result.get("data"), method="rest", requested=history_endpoint
            )
        else:
            self._unavailable(
                "rest",
                "history_api",
                method="rest",
                reason=str(result.get("error") or "request failed"),
                requested=history_endpoint,
            )

        log_start = now - timedelta(hours=self.config.log_hours)
        log_endpoint = (
            "/api/logbook/" + log_start.isoformat() + "?" + urlencode({"end_time": now.isoformat()})
        )
        result = make_ha_request(log_endpoint)
        if result.get("success"):
            self._store(
                "rest", "logbook_api", result.get("data"), method="rest", requested=log_endpoint
            )
        else:
            self._unavailable(
                "rest",
                "logbook_api",
                method="rest",
                reason=str(result.get("error") or "request failed"),
                requested=log_endpoint,
            )

        calendars = make_ha_request("/api/calendars")
        calendar_rows: list[dict[str, Any]] = []
        if calendars.get("success") and isinstance(calendars.get("data"), list):
            calendar_rows = [item for item in calendars["data"] if isinstance(item, dict)]
            self._store(
                "rest", "calendars", calendar_rows, method="rest", requested="/api/calendars"
            )
        else:
            self._unavailable(
                "rest",
                "calendars",
                method="rest",
                reason=str(calendars.get("error") or "request failed"),
                requested="/api/calendars",
            )

        events: dict[str, Any] = {}
        end = now + timedelta(days=self.config.calendar_days)
        for calendar in calendar_rows:
            entity_id = calendar.get("entity_id")
            if not isinstance(entity_id, str) or not entity_id.startswith("calendar."):
                continue
            endpoint = f"/api/calendars/{entity_id}?" + urlencode(
                {"start": now.isoformat(), "end": end.isoformat()}
            )
            result = make_ha_request(endpoint)
            source = f"calendar_events:{entity_id}"
            if result.get("success"):
                safe, redactions = redact_sensitive(result.get("data"))
                events[entity_id] = safe
                self.provenance.record(
                    source,
                    method="rest",
                    status="complete",
                    records=record_count(safe),
                    size_bytes=self._encoded_size(safe),
                    redacted_fields=redactions,
                    requested=endpoint,
                )
            else:
                events[entity_id] = None
                self.provenance.record(
                    source,
                    method="rest",
                    status="unavailable",
                    reason=str(result.get("error") or "request failed"),
                    requested=endpoint,
                )
        self._store("rest", "calendar_events", events, method="rest", source_name="calendar_events")

    @staticmethod
    def _ws_recv_json(ws: Any, timeout: float = 10.0) -> dict[str, Any]:
        raw = ws.recv(timeout=timeout)
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise RuntimeError("unexpected websocket response")
        return payload

    def _ws_command(self, ws: Any, request_id: int, command: str, extra: dict[str, Any]) -> Any:
        ws.send(json.dumps({"id": request_id, "type": command, **extra}))
        response = self._ws_recv_json(ws)
        if response.get("id") != request_id or response.get("type") != "result":
            raise RuntimeError("unexpected websocket command response")
        if response.get("success") is not True:
            error = response.get("error")
            raise RuntimeError(str(error or "websocket command failed"))
        return response.get("result")

    def _collect_websocket(self) -> None:
        try:
            from websockets.sync.client import connect

            url = self.config.ha_url.rstrip("/")
            if url.startswith("https://"):
                ws_url = "wss://" + url[len("https://") :] + "/api/websocket"
            elif url.startswith("http://"):
                ws_url = "ws://" + url[len("http://") :] + "/api/websocket"
            else:
                raise RuntimeError("HA_URL must use http or https")
            with connect(ws_url, open_timeout=10, close_timeout=3) as ws:
                auth_required = self._ws_recv_json(ws)
                if auth_required.get("type") != "auth_required":
                    raise RuntimeError("Home Assistant websocket did not request authentication")
                ws.send(json.dumps({"type": "auth", "access_token": self.config.ha_token}))
                auth = self._ws_recv_json(ws)
                if auth.get("type") != "auth_ok":
                    raise RuntimeError("Home Assistant websocket authentication failed")

                request_id = 1
                for key, command, extra in _WS_SOURCES:
                    try:
                        result = self._ws_command(ws, request_id, command, extra)
                    except Exception as exc:
                        self._unavailable(
                            "websocket",
                            key,
                            method="websocket",
                            reason=type(exc).__name__,
                            requested=command,
                        )
                    else:
                        self._store(
                            "websocket",
                            key,
                            result,
                            method="websocket",
                            requested=command,
                        )
                    request_id += 1
                request_id = self._collect_todo_items(ws, request_id)
                self._collect_weather_forecasts(ws, request_id)
        except Exception as exc:
            _logger.warning("Comprehensive websocket collection unavailable: %s", exc)
            for key, command, _ in _WS_SOURCES:
                if key not in self.data["websocket"]:
                    self._unavailable(
                        "websocket",
                        key,
                        method="websocket",
                        reason=type(exc).__name__,
                        requested=command,
                    )
            for key in ("todo_items", "weather_forecasts"):
                if key not in self.data["websocket"]:
                    self._unavailable(
                        "websocket", key, method="websocket", reason=type(exc).__name__
                    )

    def _collect_todo_items(self, ws: Any, request_id: int) -> int:
        states = self.data["rest"].get("states_api") or []
        entity_ids = sorted(
            item["entity_id"]
            for item in states
            if isinstance(item, dict)
            and isinstance(item.get("entity_id"), str)
            and item["entity_id"].startswith("todo.")
        )
        items: dict[str, Any] = {}
        total = 0
        for entity_id in entity_ids:
            source = f"todo_items:{entity_id}"
            try:
                result = self._ws_command(
                    ws, request_id, "todo/item/list", {"entity_id": entity_id}
                )
            except Exception as exc:
                items[entity_id] = None
                self.provenance.record(
                    source, method="websocket", status="unavailable", reason=type(exc).__name__
                )
            else:
                safe, redactions = redact_sensitive(result)
                items[entity_id] = safe
                count = record_count(safe.get("items") if isinstance(safe, dict) else safe)
                total += count
                self.provenance.record(
                    source,
                    method="websocket",
                    status="complete",
                    records=count,
                    size_bytes=self._encoded_size(safe),
                    redacted_fields=redactions,
                    requested="todo/item/list",
                )
            request_id += 1
        self.data["websocket"]["todo_items"] = items
        self.provenance.record(
            "todo_items",
            method="websocket",
            status="complete",
            records=total,
            size_bytes=self._encoded_size(items),
            requested="todo/item/list",
        )
        return request_id

    def _collect_weather_forecasts(self, ws: Any, request_id: int) -> int:
        states = self.data["rest"].get("states_api") or []
        forecasts: dict[str, dict[str, Any]] = {}
        total = 0
        # HA weather feature bits: forecast daily=1, hourly=2, twice_daily=4.
        feature_types = ((1, "daily"), (2, "hourly"), (4, "twice_daily"))
        for item in states:
            if not isinstance(item, dict):
                continue
            entity_id = item.get("entity_id")
            if not isinstance(entity_id, str) or not entity_id.startswith("weather."):
                continue
            raw_attrs = item.get("attributes")
            attrs: dict[str, Any] = raw_attrs if isinstance(raw_attrs, dict) else {}
            supported = attrs.get("supported_features", 0)
            try:
                feature_mask = int(supported or 0)
            except (TypeError, ValueError):
                feature_mask = 0
            per_entity: dict[str, Any] = {}
            for bit, forecast_type in feature_types:
                if not feature_mask & bit:
                    continue
                source = f"weather_forecast:{entity_id}:{forecast_type}"
                try:
                    ws.send(
                        json.dumps(
                            {
                                "id": request_id,
                                "type": "weather/subscribe_forecast",
                                "entity_id": entity_id,
                                "forecast_type": forecast_type,
                            }
                        )
                    )
                    ack = self._ws_recv_json(ws)
                    if ack.get("id") != request_id or ack.get("success") is not True:
                        raise RuntimeError("forecast subscription failed")
                    event = self._ws_recv_json(ws)
                    forecast = event.get("event", {}).get("forecast", [])
                    safe, redactions = redact_sensitive(forecast)
                    per_entity[forecast_type] = safe
                    count = record_count(safe)
                    total += count
                    self.provenance.record(
                        source,
                        method="websocket",
                        status="complete",
                        records=count,
                        size_bytes=self._encoded_size(safe),
                        redacted_fields=redactions,
                        requested="weather/subscribe_forecast",
                    )
                except Exception as exc:
                    per_entity[forecast_type] = None
                    self.provenance.record(
                        source,
                        method="websocket",
                        status="unavailable",
                        reason=type(exc).__name__,
                        requested="weather/subscribe_forecast",
                    )
                request_id += 1
            if per_entity:
                forecasts[entity_id] = per_entity
        self.data["websocket"]["weather_forecasts"] = forecasts
        self.provenance.record(
            "weather_forecasts",
            method="websocket",
            status="complete",
            records=total,
            size_bytes=self._encoded_size(forecasts),
            requested="weather/subscribe_forecast",
        )
        return request_id

    def _collect_files(self) -> None:
        root = self.config.config_path.resolve(strict=False)
        output: dict[str, Any] = {}
        total_bytes = 0
        if not root.is_dir():
            self._unavailable(
                "files", "config_tree", method="filesystem", reason="config root unavailable"
            )
            return

        for path in sorted(root.rglob("*")):
            try:
                relative = path.relative_to(root)
            except ValueError:
                continue
            if any(part.casefold() in _BLOCKED_DIRS for part in relative.parts):
                continue
            if path.is_symlink() or not path.is_file():
                continue
            name = path.name.casefold()
            if name in _BLOCKED_NAMES or any(
                name.startswith(prefix) for prefix in _BLOCKED_PREFIXES
            ):
                self.provenance.record(
                    f"file:{relative.as_posix()}",
                    method="filesystem",
                    status="skipped",
                    reason="policy: credential-bearing source blocked",
                )
                continue
            if path.suffix.casefold() not in _TEXT_SUFFIXES and ".storage" not in relative.parts:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if (
                size > self.config.max_source_bytes
                or total_bytes + size > self.config.max_source_bytes
            ):
                self.provenance.record(
                    f"file:{relative.as_posix()}",
                    method="filesystem",
                    status="partial",
                    size_bytes=size,
                    reason="aggregate source-size limit reached",
                )
                continue
            try:
                raw = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            value: Any = raw
            if path.suffix.casefold() == ".json" or ".storage" in relative.parts:
                try:
                    value = json.loads(raw)
                except json.JSONDecodeError:
                    value = raw
            safe, redactions = redact_sensitive(value)
            output[relative.as_posix()] = safe
            total_bytes += size
            self.provenance.record(
                f"file:{relative.as_posix()}",
                method="filesystem",
                status="complete",
                records=record_count(safe),
                size_bytes=self._encoded_size(safe),
                redacted_fields=redactions,
            )
        self.data["files"]["config_tree"] = output
        self.provenance.record(
            "filesystem_snapshot",
            method="filesystem",
            status="complete",
            records=len(output),
            size_bytes=self._encoded_size(output),
        )
