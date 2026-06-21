from __future__ import annotations

from .config import NodeRuntimeConfig


class PreflightFailure(RuntimeError):
    """Raised when runtime configuration is not safe to start."""


def run_preflight(config: NodeRuntimeConfig) -> None:
    """
    Validate runtime configuration relationships before starting a node runtime.
    """
    _validate_security_profile(config)
    _validate_action_configuration(config)
    _validate_attachment_configuration(config)
    _validate_gate_configuration(config)


def _validate_security_profile(config: NodeRuntimeConfig) -> None:
    if config.dev_mode:
        return

    if not config.require_signature:
        return

    if config.signing_algorithm == "hmac-sha256":
        if not config.signing_key:
            raise PreflightFailure("hmac-sha256 requires signing_key when signatures are required")
        return

    if config.signing_algorithm == "ed25519":
        if not config.signing_private_key:
            raise PreflightFailure(
                "ed25519 requires signing_private_key when signatures are required"
            )
        return

    raise PreflightFailure(f"unsupported signing algorithm: {config.signing_algorithm}")


def _validate_action_configuration(config: NodeRuntimeConfig) -> None:
    allowed_actions = set(config.allowed_actions)
    required_idempotency = set(config.require_idempotency_for_actions)

    if required_idempotency and not allowed_actions:
        raise PreflightFailure(
            "require_idempotency_for_actions is configured but allowed_actions is empty"
        )

    if not required_idempotency.issubset(allowed_actions):
        invalid = sorted(required_idempotency - allowed_actions)
        joined = ", ".join(invalid)
        raise PreflightFailure(
            "require_idempotency_for_actions contains actions"
            f" not present in allowed_actions: {joined}"
        )

    if "response" in allowed_actions or "failure" in allowed_actions:
        raise PreflightFailure(
            "allowed_actions must list executable inbound actions only, not response packet types"
        )


def _validate_attachment_configuration(config: NodeRuntimeConfig) -> None:
    if config.max_attachment_size_bytes > config.max_packet_bytes:
        raise PreflightFailure("max_attachment_size_bytes must not exceed max_packet_bytes")

    if config.max_attachments > 0 and not config.attachment_allowed_schemes:
        raise PreflightFailure(
            "attachment_allowed_schemes must be configured when attachments are enabled"
        )


def _validate_gate_configuration(config: NodeRuntimeConfig) -> None:
    if config.gate_url is None:
        return
    if not (config.gate_url.startswith("http://") or config.gate_url.startswith("https://")):
        raise PreflightFailure("gate_url must start with http:// or https://")
