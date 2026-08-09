"""Regression tests for final model-visible response redaction."""

import json

from tools.manifests import get_all_manifests, make_manifest, register_manifest, set_active_tools
from tools.operations import OperationRegistry
from tools.redaction import REDACTED, sanitize_response_data


def _register(name: str) -> None:
    manifest = make_manifest(name)
    manifest["extensions"]["target_binding"] = {
        "kind": "deployment-resource",
        "target": "runtime",
        "revalidation": "per-invocation",
    }
    active = set(get_all_manifests(active_only=True))
    register_manifest(name, manifest)
    set_active_tools(active | {name})


def test_key_aware_redaction_removes_plain_secret_values() -> None:
    payload = sanitize_response_data(
        {
            "password": "plain-secret",
            "nested": {
                "access_token": "token-value",
                "client_secret": "client-value",
                "safe_name": "living room",
            },
        }
    )
    assert payload["password"] == REDACTED
    assert payload["nested"]["access_token"] == REDACTED
    assert payload["nested"]["client_secret"] == REDACTED
    assert payload["nested"]["safe_name"] == "living room"


def test_operation_boundary_redacts_config_entry_credentials() -> None:
    name = "test_final_redaction_boundary"
    _register(name)

    def raw_tool() -> str:
        return json.dumps(
            {
                "success": True,
                "entries": [
                    {
                        "entry_id": "safe-id",
                        "options": {
                            "password": "supersecret",
                            "refresh_token": "refresh-secret",
                            "label": "safe-value",
                        },
                    }
                ],
            }
        )

    registry = OperationRegistry()
    operation = registry.register(name, raw_tool)
    result = json.loads(operation.fn())
    options = result["entries"][0]["options"]
    assert options["password"] == REDACTED
    assert options["refresh_token"] == REDACTED
    assert options["label"] == "safe-value"
    assert "supersecret" not in json.dumps(result)
    assert "refresh-secret" not in json.dumps(result)
