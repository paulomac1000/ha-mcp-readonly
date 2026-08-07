"""Authentication adapters for network MCP transports."""

from __future__ import annotations

import hmac
from fastmcp.server.auth import AccessToken, TokenVerifier


class ConfiguredBearerTokenVerifier(TokenVerifier):
    """Verify one deployment-provided bearer token without logging it."""

    def __init__(self, expected_token: str) -> None:
        if not expected_token:
            raise ValueError("A non-empty MCP_AUTH_TOKEN is required")
        super().__init__(required_scopes=["ha.read"])
        self._expected_token = expected_token

    async def verify_token(self, token: str) -> AccessToken | None:
        if not hmac.compare_digest(token.encode("utf-8"), self._expected_token.encode("utf-8")):
            return None
        return AccessToken(
            token=token,
            client_id="configured-client",
            subject="authenticated-mcp-client",
            scopes=["ha.read", "filesystem.read", "artifact.read"],
        )
