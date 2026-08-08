from __future__ import annotations

import json
import threading
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from context_generator.config import GenerationConfig
from context_generator.provenance import ProvenanceTracker
from context_generator.runtime import GenerationRuntime, generation_scope
from context_generator.snapshot import ComprehensiveSnapshotCollector


class _RecordedHAHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.headers.get("Authorization") != "Bearer upstream-contract-token":
            self.send_response(401)
            self.end_headers()
            return
        path = self.path.split("?", 1)[0]
        payloads: dict[str, Any] = {
            "/api/config": {"version": "2026.8.0", "location_name": "Contract HA"},
            "/api/states": [
                {"entity_id": "todo.contract", "state": "2", "attributes": {}},
                {
                    "entity_id": "weather.contract",
                    "state": "sunny",
                    "attributes": {"supported_features": 7},
                },
            ],
            "/api/services": [{"domain": "light", "services": {"turn_on": {}}}],
            "/api/events": [{"event": "state_changed", "listener_count": 1}],
            "/api/components": ["light", "weather", "todo"],
            "/api/system_health": {"homeassistant": {"version": "2026.8.0"}},
            "/api/energy/dashboard": {"energy_sources": []},
            "/api/error_log": "contract log line",
            "/api/calendars": [{"entity_id": "calendar.contract", "name": "Contract"}],
        }
        if path.startswith("/api/history/period/"):
            payload: Any = [[{"entity_id": "sensor.contract", "state": "1"}]]
        elif path.startswith("/api/logbook/"):
            payload = [{"entity_id": "sensor.contract", "state": "1"}]
        elif path == "/api/calendars/calendar.contract":
            payload = [{"summary": "Contract event", "start": "2026-08-08T12:00:00+00:00"}]
        else:
            payload = payloads.get(path)
        if payload is None:
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


@contextmanager
def _recorded_http_server() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RecordedHAHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


class _RecordedWebSocket:
    def __init__(self) -> None:
        self.queue: deque[str] = deque([json.dumps({"type": "auth_required"})])

    def __enter__(self) -> _RecordedWebSocket:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def send(self, raw: str) -> None:
        message = json.loads(raw)
        if message.get("type") == "auth":
            assert message["access_token"] == "upstream-contract-token"
            self.queue.append(json.dumps({"type": "auth_ok"}))
            return
        request_id = message["id"]
        command = message["type"]
        results: dict[str, Any] = {
            "config/area_registry/list": [{"area_id": "contract", "name": "Contract"}],
            "config/device_registry/list": [{"id": "device-contract", "name": "Contract"}],
            "config/entity_registry/list": [
                {"entity_id": "sensor.contract", "platform": "template"}
            ],
            "config/floor_registry/list": [{"floor_id": "ground", "name": "Ground"}],
            "config/label_registry/list": [{"label_id": "important", "name": "Important"}],
            "config/category_registry/list": [],
            "config_entries/get": [{"entry_id": "entry-contract", "domain": "template"}],
            "get_panels": {"lovelace": {"title": "Overview"}},
            "lovelace/resources": [{"id": "resource-1", "url": "/local/card.js"}],
            "assist_pipeline/pipeline/list": {"pipelines": []},
            "energy/get_prefs": {"energy_sources": []},
            "repairs/list_issues": {"issues": []},
            "system_health/info": {"homeassistant": {"version": "2026.8.0"}},
            "todo/item/list": {"items": [{"uid": "one", "summary": "Contract task"}]},
        }
        if command == "weather/subscribe_forecast":
            self.queue.append(
                json.dumps({"id": request_id, "type": "result", "success": True, "result": None})
            )
            self.queue.append(
                json.dumps(
                    {
                        "id": request_id,
                        "type": "event",
                        "event": {"forecast": [{"datetime": "2026-08-09T00:00:00+00:00"}]},
                    }
                )
            )
            return
        self.queue.append(
            json.dumps(
                {
                    "id": request_id,
                    "type": "result",
                    "success": True,
                    "result": results[command],
                }
            )
        )

    def recv(self, timeout: float = 10.0) -> str:
        del timeout
        return self.queue.popleft()


def test_recorded_home_assistant_rest_and_websocket_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import websockets.sync.client

    monkeypatch.setattr(websockets.sync.client, "connect", lambda *a, **k: _RecordedWebSocket())
    (tmp_path / "configuration.yaml").write_text(
        "homeassistant:\n  name: Contract\n", encoding="utf-8"
    )
    provenance = ProvenanceTracker()
    with _recorded_http_server() as ha_url:
        config = GenerationConfig(
            config_path=tmp_path,
            output_path=tmp_path / "context.md",
            ha_url=ha_url,
            ha_token="upstream-contract-token",
            mode="online",
            history_hours=1,
            log_hours=1,
            calendar_days=1,
        )
        runtime = GenerationRuntime(config=config, provenance=provenance)
        with generation_scope(runtime):
            snapshot = ComprehensiveSnapshotCollector(config, provenance).collect()

    assert snapshot["rest"]["config_api"]["version"] == "2026.8.0"
    assert (
        snapshot["rest"]["calendar_events"]["calendar.contract"][0]["summary"] == "Contract event"
    )
    assert snapshot["websocket"]["areas_ws"][0]["area_id"] == "contract"
    assert snapshot["websocket"]["todo_items"]["todo.contract"]["items"][0]["uid"] == "one"
    weather = snapshot["websocket"]["weather_forecasts"]["weather.contract"]
    assert set(weather) == {"daily", "hourly", "twice_daily"}
    summary = provenance.summary()
    assert summary["counts"].get("unavailable", 0) == 0
