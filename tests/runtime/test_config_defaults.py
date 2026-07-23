"""Regression tests for NodeRuntimeConfig default consistency.

Prior to the fix, the field defaults were mutually contradictory:
``max_attachment_size_bytes`` defaulted to 10 MiB while ``max_packet_bytes``
defaulted to 256 KiB, and ``max_attachments`` defaulted to 32 while
``attachment_allowed_schemes`` defaulted to empty — so constructing the model
without explicit attachment overrides raised ``ValidationError`` at import
time in every consumer (observed as EIE's app-wide import crash, fixed
downstream in EIE PR #136; this fixes the root cause upstream).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from constellation_node_sdk.runtime.config import NodeRuntimeConfig, get_runtime_config


def _minimal(**overrides: object) -> NodeRuntimeConfig:
    kwargs: dict[str, object] = {
        "environment": "local",
        "node_name": "test-node",
        "service_name": "test-service",
        "service_version": "1.0.0",
    }
    kwargs.update(overrides)
    return NodeRuntimeConfig(**kwargs)


class TestDefaultConsistency:
    def test_default_construction_succeeds(self) -> None:
        """The model must be constructible with only the required fields."""
        config = _minimal()
        assert config.max_attachments == 0
        assert config.max_attachment_size_bytes == 0
        assert config.attachment_allowed_schemes == ()

    def test_defaults_disable_attachments(self) -> None:
        config = _minimal()
        assert config.max_attachments == 0, "attachments must be opt-in"

    def test_attachment_size_default_within_packet_ceiling(self) -> None:
        config = _minimal()
        assert config.max_attachment_size_bytes <= config.max_packet_bytes

    def test_get_runtime_config_env_defaults_succeed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_runtime_config() with a clean environment must not raise."""
        for var in (
            "L9_MAX_ATTACHMENTS",
            "L9_MAX_ATTACHMENT_SIZE_BYTES",
            "L9_ATTACHMENT_ALLOWED_SCHEMES",
            "L9_MAX_PACKET_BYTES",
        ):
            monkeypatch.delenv(var, raising=False)
        get_runtime_config.cache_clear()
        try:
            config = get_runtime_config()
            assert config.max_attachments == 0
            assert config.max_attachment_size_bytes == 0
        finally:
            get_runtime_config.cache_clear()


class TestAttachmentOptInStillValidated:
    def test_enabling_attachments_without_schemes_rejected(self) -> None:
        with pytest.raises(ValidationError, match="attachment_allowed_schemes"):
            _minimal(max_attachments=4, max_attachment_size_bytes=1024)

    def test_attachment_size_exceeding_packet_rejected(self) -> None:
        with pytest.raises(ValidationError, match="max_attachment_size_bytes"):
            _minimal(
                max_attachments=4,
                max_attachment_size_bytes=10_485_760,
                attachment_allowed_schemes=("https",),
            )

    def test_valid_opt_in_accepted(self) -> None:
        config = _minimal(
            max_attachments=4,
            max_attachment_size_bytes=65_536,
            attachment_allowed_schemes=("https",),
        )
        assert config.max_attachments == 4
        assert config.attachment_allowed_schemes == ("https",)
