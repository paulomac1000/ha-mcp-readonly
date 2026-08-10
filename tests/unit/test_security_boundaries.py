"""Unit tests for bearer credential validation."""

from tools.security import bearer_token_is_valid


def test_bearer_token_validation() -> None:
    assert bearer_token_is_valid({"authorization": "Bearer s3cret"}, "s3cret") is True
    assert bearer_token_is_valid({"authorization": "Bearer wrong"}, "s3cret") is False
    assert bearer_token_is_valid({}, "s3cret") is False
    assert bearer_token_is_valid({"authorization": "Basic abc"}, "s3cret") is False
    assert bearer_token_is_valid({"authorization": "Bearer s3cret"}, "") is False


def test_non_ascii_bearer_token_fails_closed() -> None:
    assert bearer_token_is_valid({"authorization": "Bearer żółć"}, "ascii-token") is False
