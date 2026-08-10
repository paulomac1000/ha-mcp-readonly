from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one target, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_between(path: str, start: str, end: str, replacement: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    left = text.find(start)
    if left < 0:
        raise SystemExit(f"{path}: start marker missing")
    right = text.find(end, left + len(start))
    if right < 0:
        raise SystemExit(f"{path}: end marker missing")
    target.write_text(text[:left] + replacement + text[right:], encoding="utf-8")


# A successful weather subscription must be explicitly removed. If the stream
# becomes uncertain after subscription, abort the websocket session so closing
# the connection is the cleanup rather than continuing on a desynchronized stream.
weather = '''    def _collect_weather_forecasts(self, ws: Any, request_id: int) -> int:
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
                subscription_id = request_id
                unsubscribe_id = request_id + 1
                request_id += 2
                subscribed = False
                try:
                    ws.send(
                        json.dumps(
                            {
                                "id": subscription_id,
                                "type": "weather/subscribe_forecast",
                                "entity_id": entity_id,
                                "forecast_type": forecast_type,
                            }
                        )
                    )
                    ack = self._ws_recv_json(ws)
                    if ack.get("id") != subscription_id or ack.get("type") != "result":
                        raise WebSocketProtocolError("unexpected forecast subscription response")
                    if ack.get("success") is not True:
                        raise RuntimeError("forecast subscription failed")
                    subscribed = True
                    event = self._ws_recv_json(ws)
                    if event.get("id") != subscription_id or event.get("type") != "event":
                        raise WebSocketProtocolError("unexpected forecast subscription event")
                    event_payload = event.get("event")
                    if not isinstance(event_payload, dict):
                        raise WebSocketProtocolError("forecast event payload is invalid")
                    forecast = event_payload.get("forecast", [])
                    safe, redactions = redact_sensitive(forecast)
                    self._ws_command(
                        ws,
                        unsubscribe_id,
                        "unsubscribe_events",
                        {"subscription": subscription_id},
                    )
                    subscribed = False
                except WebSocketProtocolError:
                    raise
                except Exception as exc:
                    if subscribed:
                        raise WebSocketProtocolError(
                            "weather forecast subscription stream became unsafe"
                        ) from exc
                    per_entity[forecast_type] = None
                    self.provenance.record(
                        source,
                        method="websocket",
                        status="unavailable",
                        reason=type(exc).__name__,
                        requested="weather/subscribe_forecast",
                    )
                else:
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

'''
replace_between(
    "context_generator/snapshot.py",
    "    def _collect_weather_forecasts(self, ws: Any, request_id: int) -> int:\n",
    "    def _collect_files(self) -> None:\n",
    weather,
)

# The helper no longer falls back to process-global credentials. Unit tests must
# model the same explicit generation scope used by production callers.
replace_once(
    "tests/unit/test_context_generator.py",
    '''    def test_make_ha_request(self):
        from context_generator.utils import make_ha_request

        with patch("context_generator.utils.requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.json.return_value = {"data": "ok"}
            mock_response.raise_for_status = MagicMock()
            mock_get.return_value = mock_response

            result = make_ha_request("/api/states")
            assert result["success"] is True
''',
    '''    def test_make_ha_request(self, tmp_path):
        from context_generator.provenance import ProvenanceTracker
        from context_generator.runtime import GenerationRuntime, generation_scope
        from context_generator.utils import make_ha_request

        config = GenerationConfig(
            config_path=tmp_path,
            output_path=tmp_path / "out.md",
            ha_url="http://ha:8123",
            ha_token="token",
            mode="online",
        )
        runtime = GenerationRuntime(config=config, provenance=ProvenanceTracker())
        with patch("context_generator.utils.requests.get") as mock_get, generation_scope(runtime):
            mock_response = MagicMock()
            mock_response.json.return_value = {"data": "ok"}
            mock_response.raise_for_status = MagicMock()
            mock_get.return_value = mock_response

            result = make_ha_request("/api/states")
            assert result["success"] is True
            mock_get.assert_called_once()
            assert mock_get.call_args.args[0] == "http://ha:8123/api/states"
            assert mock_get.call_args.kwargs["headers"]["Authorization"] == "Bearer token"
''',
)

# Unit websocket fake: model both subscription events and unsubscribe results.
replace_once(
    "tests/unit/test_context_generator.py",
    '''            def send(self, value):
                request = json.loads(value)
                self.sent.append(request)
                self.responses.extend(
                    [
                        json.dumps(
                            {
                                "id": request["id"],
                                "type": "result",
                                "success": True,
                                "result": None,
                            }
                        ),
                        json.dumps(
                            {
                                "id": request["id"],
                                "type": "event",
                                "event": {
                                    "type": request["forecast_type"],
                                    "forecast": [
                                        {
                                            "datetime": "2026-08-07T12:00:00+00:00",
                                            "condition": "sunny",
                                        }
                                    ],
                                },
                            }
                        ),
                    ]
                )
''',
    '''            def send(self, value):
                request = json.loads(value)
                self.sent.append(request)
                if request["type"] == "unsubscribe_events":
                    self.responses.append(
                        json.dumps(
                            {
                                "id": request["id"],
                                "type": "result",
                                "success": True,
                                "result": None,
                            }
                        )
                    )
                    return
                self.responses.extend(
                    [
                        json.dumps(
                            {
                                "id": request["id"],
                                "type": "result",
                                "success": True,
                                "result": None,
                            }
                        ),
                        json.dumps(
                            {
                                "id": request["id"],
                                "type": "event",
                                "event": {
                                    "type": request["forecast_type"],
                                    "forecast": [
                                        {
                                            "datetime": "2026-08-07T12:00:00+00:00",
                                            "condition": "sunny",
                                        }
                                    ],
                                },
                            }
                        ),
                    ]
                )
''',
)
replace_once(
    "tests/unit/test_context_generator.py",
    '''        assert next_id == 22
        assert [item["forecast_type"] for item in ws.sent] == ["daily", "hourly"]
''',
    '''        assert next_id == 24
        subscribe_requests = [
            item for item in ws.sent if item["type"] == "weather/subscribe_forecast"
        ]
        unsubscribe_requests = [item for item in ws.sent if item["type"] == "unsubscribe_events"]
        assert [item["forecast_type"] for item in subscribe_requests] == ["daily", "hourly"]
        assert [item["id"] for item in unsubscribe_requests] == [21, 23]
        assert [item["subscription"] for item in unsubscribe_requests] == [20, 22]
''',
)

# Recorded HA websocket contract now includes the protocol-required unsubscribe ack.
for marker in (
    '''        if command == "weather/subscribe_forecast":
            self.queue.append(
''',
):
    pass
replace_once(
    "tests/protocol/test_home_assistant_upstream_contract.py",
    '''        if command == "weather/subscribe_forecast":
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
''',
    '''        if command == "weather/subscribe_forecast":
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
        if command == "unsubscribe_events":
            self.queue.append(
                json.dumps({"id": request_id, "type": "result", "success": True, "result": None})
            )
            return
        self.queue.append(
''',
)
# Same behavior for the cassette subclass, which overrides send().
replace_once(
    "tests/protocol/test_home_assistant_upstream_contract.py",
    '''        if command == "weather/subscribe_forecast":
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
        result = self._cassette.get(command, [])
''',
    '''        if command == "weather/subscribe_forecast":
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
        if command == "unsubscribe_events":
            self.queue.append(
                json.dumps({"id": request_id, "type": "result", "success": True, "result": None})
            )
            return
        result = self._cassette.get(command, [])
''',
)

# Restore the MCP composition singleton together with manifest globals. Otherwise a
# unit test can leave a 158-tool server object paired with a pre-composition manifest
# snapshot, which makes subsequent protocol introspection internally inconsistent.
replace_once(
    "tests/unit/conftest.py",
    "import json\n",
    "import json\nimport sys\n",
)
replace_once(
    "tests/unit/conftest.py",
    '''    manifests = copy.deepcopy(manifests_module._TOOL_MANIFESTS)
    active = manifests_module._ACTIVE_TOOL_NAMES
    reasons = dict(manifests_module._INACTIVE_REASONS)
    try:
        yield
    finally:
        manifests_module._TOOL_MANIFESTS.clear()
        manifests_module._TOOL_MANIFESTS.update(manifests)
        manifests_module._ACTIVE_TOOL_NAMES = active
        manifests_module._INACTIVE_REASONS.clear()
        manifests_module._INACTIVE_REASONS.update(reasons)
''',
    '''    manifests = copy.deepcopy(manifests_module._TOOL_MANIFESTS)
    active = manifests_module._ACTIVE_TOOL_NAMES
    reasons = dict(manifests_module._INACTIVE_REASONS)
    server_module = sys.modules.get("server")
    server_state = None
    if server_module is not None:
        server_state = (
            getattr(server_module, "_MCP_SERVER", None),
            dict(getattr(server_module, "_TOOL_CATALOG", {})),
            copy.deepcopy(getattr(server_module, "HEALTH_STATE", {})),
        )
    try:
        yield
    finally:
        manifests_module._TOOL_MANIFESTS.clear()
        manifests_module._TOOL_MANIFESTS.update(manifests)
        manifests_module._ACTIVE_TOOL_NAMES = active
        manifests_module._INACTIVE_REASONS.clear()
        manifests_module._INACTIVE_REASONS.update(reasons)
        if server_module is not None and server_state is not None:
            mcp_server, catalog, health = server_state
            server_module._MCP_SERVER = mcp_server
            server_module._TOOL_CATALOG.clear()
            server_module._TOOL_CATALOG.update(catalog)
            server_module.HEALTH_STATE.clear()
            server_module.HEALTH_STATE.update(health)
''',
)

# MCP list_tools enumerates the supported transport catalog; capability `tool_count`
# is deployment-active. Keep those contracts distinct in the protocol test.
replace_once(
    "tests/protocol/test_mcp_protocol.py",
    '''            assert payload["success"] is True
            assert payload["tool_count"] == expected
            assert payload["transports"] == ["stdio", "streamable-http"]
''',
    '''            assert payload["success"] is True
            assert payload["supported_tool_count"] == expected
            assert len(payload["tools"]) == expected
            assert payload["tool_count"] == payload["active_tool_count"]
            assert 0 < payload["active_tool_count"] <= expected
            assert payload["transports"] == ["stdio", "streamable-http"]
''',
)
