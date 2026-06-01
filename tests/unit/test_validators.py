"""
Tests for tools/validators.py
"""

import os

import pytest

from tools.validators import (
    ValidationError,
    validate_entity_id,
    validate_nonempty,
    validate_path,
    validate_port,
)


class TestValidationError:
    def test_is_exception(self):
        err = ValidationError("test error")
        assert isinstance(err, Exception)

    def test_message(self):
        err = ValidationError("custom validation message")
        assert str(err) == "custom validation message"

    def test_can_be_raised(self):
        with pytest.raises(ValidationError):
            raise ValidationError("test")

    def test_can_be_caught(self):
        try:
            raise ValidationError("caught")
        except ValidationError as e:
            assert str(e) == "caught"


class TestValidatePath:
    def test_normal_path(self):
        result = validate_path("/config/automations.yaml")
        assert result.endswith("automations.yaml")

    def test_blocks_dot_dot(self):
        with pytest.raises(ValidationError, match="Path traversal"):
            validate_path("/config/../secrets.yaml")

    def test_blocks_tilde(self):
        with pytest.raises(ValidationError, match="Home directory"):
            validate_path("~/config/secrets.yaml")

    def test_blocks_empty_string(self):
        with pytest.raises(ValidationError, match="non-empty string"):
            validate_path("")

    def test_blocks_none(self):
        with pytest.raises(ValidationError, match="non-empty string"):
            validate_path(None)

    def test_blocks_dot_dot_deep(self):
        with pytest.raises(ValidationError, match="Path traversal"):
            validate_path("/config/sub/../../../etc/passwd")

    def test_with_allowed_dirs_allows_valid(self, tmp_path):
        allowed = [str(tmp_path / "sub")]
        valid_path = str(tmp_path / "sub" / "file.txt")
        os.makedirs(tmp_path / "sub", exist_ok=True)
        (tmp_path / "sub" / "file.txt").write_text("test")
        result = validate_path(str(valid_path), allowed_dirs=allowed)
        assert result == valid_path

    def test_with_allowed_dirs_rejects_other(self, tmp_path):
        allowed = [str(tmp_path / "allowed")]
        other = str(tmp_path / "other" / "file.txt")
        os.makedirs(tmp_path / "other", exist_ok=True)
        (tmp_path / "other" / "file.txt").write_text("test")
        with pytest.raises(ValidationError, match="not in allowed"):
            validate_path(other, allowed_dirs=allowed)


class TestValidatePort:
    def test_valid_port(self):
        assert validate_port(8080) == 8080

    def test_min_port(self):
        assert validate_port(1) == 1

    def test_max_port(self):
        assert validate_port(65535) == 65535

    def test_zero_rejected(self):
        with pytest.raises(ValidationError, match="1-65535"):
            validate_port(0)

    def test_too_high_rejected(self):
        with pytest.raises(ValidationError, match="1-65535"):
            validate_port(65536)

    def test_negative_rejected(self):
        with pytest.raises(ValidationError, match="1-65535"):
            validate_port(-1)


class TestValidateNonempty:
    def test_valid_string(self):
        assert validate_nonempty("hello") == "hello"

    def test_blocks_empty(self):
        with pytest.raises(ValidationError, match="must not be empty"):
            validate_nonempty("")

    def test_blocks_whitespace(self):
        with pytest.raises(ValidationError, match="must not be empty"):
            validate_nonempty("   ")

    def test_blocks_none(self):
        with pytest.raises(ValidationError, match="must not be empty"):
            validate_nonempty(None)

    def test_custom_name_in_error(self):
        with pytest.raises(ValidationError, match="entity_id must not be empty"):
            validate_nonempty("", name="entity_id")


class TestValidateEntityId:
    def test_valid_entity_id(self):
        assert validate_entity_id("sensor.temperature") == "sensor.temperature"

    def test_blocks_no_dot(self):
        with pytest.raises(ValidationError, match="Invalid entity_id format"):
            validate_entity_id("temperature")

    def test_blocks_empty(self):
        with pytest.raises(ValidationError, match="non-empty string"):
            validate_entity_id("")

    def test_blocks_none(self):
        with pytest.raises(ValidationError, match="non-empty string"):
            validate_entity_id(None)

    def test_complex_entity_id(self):
        assert (
            validate_entity_id("light.yeelink_color2_0510_light")
            == "light.yeelink_color2_0510_light"
        )
