from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import httpx

from constellation_node_sdk.security.signing import sign_transport_packet
from constellation_node_sdk.security.validation import validate_transport_packet
from constellation_node_sdk.transport.packet import TransportPacket

from .config import GateClientConfig
from .policy import validate_outbound_gate_packet


class GateClient:
    """
    Canonical Gate-only transport client for nodes.

    This client is the only allowed outbound inter-node transport surface.
    It never accepts an arbitrary peer URL.
    """

    def __init__(self, config: GateClientConfig) -> None:
        self._config = config

    @property
    def gate_url(self) -> str:
        return self._config.gate_url

    def _transport_key_resolver(
        self,
    ) -> Callable[[str | None], str | bytes | None] | Mapping[str, str | bytes] | None:
        if not self._config.verifying_keys and self._config.signing_key is None:
            return None
        return self._config.resolve_verifying_key

    def _maybe_sign(self, packet: TransportPacket) -> TransportPacket:
        if self._config.signing_key is None:
            return packet
        if self._config.signing_key_id is None or self._config.signing_algorithm is None:
            raise ValueError(
                "signing_key_id and signing_algorithm are required when signing_key is configured"
            )
        return sign_transport_packet(
            packet,
            key=self._config.signing_key,
            key_id=self._config.signing_key_id,
            algorithm=self._config.signing_algorithm,
        )

    def _validate_outbound(self, packet: TransportPacket) -> None:
        validate_outbound_gate_packet(
            packet,
            local_node=self._config.local_node,
            gate_node_name=self._config.allowed_gate_destination,
        )
        validate_transport_packet(
            packet,
            key_resolver=self._transport_key_resolver(),
            require_signature=self._config.require_signature,
            dev_mode=not self._config.require_signature,
            verify_hop_signatures=self._config.verify_hop_signatures,
        )

    def _validate_inbound_response(self, packet: TransportPacket) -> None:
        validate_transport_packet(
            packet,
            key_resolver=self._transport_key_resolver(),
            require_signature=self._config.verify_response_signatures,
            dev_mode=not self._config.verify_response_signatures,
            verify_hop_signatures=self._config.verify_hop_signatures,
        )

    async def send_to_gate(self, packet: TransportPacket) -> TransportPacket:
        """
        Send a canonical TransportPacket to Gate and decode a canonical TransportPacket response.
        """
        self._validate_outbound(packet)
        signed_packet = self._maybe_sign(packet)

        async with httpx.AsyncClient(timeout=self._config.timeout_seconds) as client:
            response = await client.post(
                f"{self._config.gate_url}/v1/execute",
                json=signed_packet.model_dump_json_dict(),
                headers={"Content-Type": "application/json"},
            )

        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise ValueError("Gate response body must be a JSON object")

        response_packet = TransportPacket.model_validate(body)
        self._validate_inbound_response(response_packet)
        return response_packet

    async def health(self) -> dict[str, Any]:
        """
        Query Gate health endpoint.
        """
        async with httpx.AsyncClient(timeout=self._config.timeout_seconds) as client:
            response = await client.get(f"{self._config.gate_url}/v1/health")
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise ValueError("Gate health response must be a JSON object")
        return body
