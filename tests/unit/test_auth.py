"""Unit tests for tools/auth.py — bearer token verification for network transports."""

import asyncio

import pytest

from tools.auth import ConfiguredBearerTokenVerifier


def test_empty_expected_token_is_rejected() -> None:
    with pytest.raises(ValueError):
        ConfiguredBearerTokenVerifier("")


def test_valid_token_yields_authenticated_principal() -> None:
    verifier = ConfiguredBearerTokenVerifier("s3cret-token")

    async def verify() -> None:
        result = await verifier.verify_token("s3cret-token")
        assert result is not None
        assert result.subject == "authenticated-mcp-client"
        assert result.client_id == "configured-client"
        assert set(result.scopes) == {"ha.read", "filesystem.read", "artifact.read"}

    asyncio.run(verify())


def test_invalid_token_is_rejected() -> None:
    verifier = ConfiguredBearerTokenVerifier("s3cret-token")

    async def verify() -> None:
        assert await verifier.verify_token("wrong-token") is None
        assert await verifier.verify_token("") is None
        assert await verifier.verify_token("S3CRET-TOKEN") is None

    asyncio.run(verify())


def test_configured_scopes_include_read_capabilities() -> None:
    verifier = ConfiguredBearerTokenVerifier("token")
    assert verifier.required_scopes == ["ha.read"]


@pytest.mark.asyncio
async def test_non_ascii_token_fails_closed_without_compare_digest_exception() -> None:
    verifier = ConfiguredBearerTokenVerifier("ascii-token")
    assert await verifier.verify_token("żółć") is None
