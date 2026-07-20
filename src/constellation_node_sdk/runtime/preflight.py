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
    _validate_gate_ingress_authentication(config)


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
    execute_allowed_actions = set(config.execute_allowed_actions)
    known_actions = allowed_actions | execute_allowed_actions
    required_idempotency = set(config.require_idempotency_for_actions)

    if required_idempotency and not known_actions:
        raise PreflightFailure(
            "require_idempotency_for_actions is configured but allowed_actions and"
            " execute_allowed_actions are both empty"
        )

    if not required_idempotency.issubset(known_actions):
        invalid = sorted(required_idempotency - known_actions)
        joined = ", ".join(invalid)
        raise PreflightFailure(
            "require_idempotency_for_actions contains actions not present"
            f" in allowed_actions or execute_allowed_actions: {joined}"
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


def _validate_gate_ingress_authentication(config: NodeRuntimeConfig) -> None:
    """
    Gate-only ingress trust (provenance.resolved_by_gate, address.source_node,
    provenance.route_kind) is caller-supplied and carries no authentication by
    itself. If enforce_gate_only_ingress is relied upon without also requiring
    a verified transport signature, any direct client can forge these claims
    and bypass Gate-mediated routing entirely. Fail closed outside dev_mode.
    """
    if config.dev_mode or not config.enforce_gate_only_ingress:
        return

    execute_require_signature = (
        config.execute_require_signature
        if config.execute_require_signature is not None
        else config.require_signature
    )
    if not execute_require_signature:
        raise PreflightFailure(
            "enforce_gate_only_ingress=true requires a verified signature on /v1/execute "
            "(set require_signature=true or execute_require_signature=true); "
            "unsigned Gate-only ingress trusts unauthenticated caller-supplied provenance"
        )

    if not config.enable_relay_route:
        return

    relay_require_signature = (
        config.relay_require_signature
        if config.relay_require_signature is not None
        else config.require_signature
    )
    if not relay_require_signature:
        raise PreflightFailure(
            "enforce_gate_only_ingress=true requires a verified signature on /v1/relay "
            "(set require_signature=true or relay_require_signature=true); "
            "unsigned Gate-only ingress trusts unauthenticated caller-supplied provenance"
        )
