from __future__ import annotations

from constellation_node_sdk.gate.admission import ADMISSION_ACTION, ConsumerAccessReceipt
from constellation_node_sdk.gate.capabilities import (
    CapabilityDescriptor,
    CapabilityListResponse,
)
from constellation_node_sdk.gate.capability_client import GateCapabilityClient
from constellation_node_sdk.gate.client import GateClient, activate_consumer
from constellation_node_sdk.gate.config import (
    GateClientConfig,
    GateRegistrationConfig,
    get_gate_client_config_from_env,
    get_gate_registration_config_from_env,
)
from constellation_node_sdk.gate.errors import (
    GateAuthorizationError,
    GateClientError,
    GateConfigurationError,
    GateConnectionError,
    GateHTTPError,
    GatePolicyError,
    GateRegistrationError,
    GateResponseError,
    GateSecurityError,
    GateTimeoutError,
)
from constellation_node_sdk.gate.policy import (
    assert_gate_only_destination,
    assert_local_node_identity,
    assert_node_origin_packet,
    validate_outbound_gate_packet,
)
from constellation_node_sdk.gate.registration import (
    NodeRegistration,
    build_node_registration,
    build_registration_payload,
    load_node_spec,
    register_from_env,
    register_node,
    register_with_gate,
)

__all__ = [
    "ADMISSION_ACTION",
    "CapabilityDescriptor",
    "CapabilityListResponse",
    "ConsumerAccessReceipt",
    "GateAuthorizationError",
    "GateCapabilityClient",
    "GateClient",
    "GateClientConfig",
    "GateClientError",
    "GateConfigurationError",
    "GateConnectionError",
    "GateHTTPError",
    "GatePolicyError",
    "GateRegistrationConfig",
    "GateRegistrationError",
    "GateResponseError",
    "GateSecurityError",
    "GateTimeoutError",
    "NodeRegistration",
    "activate_consumer",
    "assert_gate_only_destination",
    "assert_local_node_identity",
    "assert_node_origin_packet",
    "build_node_registration",
    "build_registration_payload",
    "get_gate_client_config_from_env",
    "get_gate_registration_config_from_env",
    "load_node_spec",
    "register_from_env",
    "register_node",
    "register_with_gate",
    "validate_outbound_gate_packet",
]
