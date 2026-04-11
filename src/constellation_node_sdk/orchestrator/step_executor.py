from __future__ import annotations

import asyncio
from typing import Any

from constellation_node_sdk.gate.client import GateClient
from constellation_node_sdk.transport.packet import TransportPacket

from .packet_builder import build_step_packet
from .retry import RetryPolicy, should_retry


class StepExecutionError(RuntimeError):
    """
    Raised when an orchestrator step cannot be completed successfully.
    """


class StepExecutor:
    """
    Execute atomic workflow steps by sending child TransportPackets to Gate.

    This class never resolves peer nodes directly. It relies entirely on Gate
    to route the requested action.
    """

    def __init__(self, *, gate_client: GateClient, source_node: str) -> None:
        normalized_source = source_node.strip().lower()
        if not normalized_source:
            raise ValueError("source_node must not be empty")

        self._gate_client = gate_client
        self._source_node = normalized_source

    @property
    def source_node(self) -> str:
        return self._source_node

    async def execute_step(
        self,
        *,
        parent: TransportPacket,
        action: str,
        payload: dict[str, Any],
        retry_policy: RetryPolicy | None = None,
    ) -> TransportPacket:
        """
        Execute a single orchestrator step through Gate with optional retries.
        """
        policy = retry_policy or RetryPolicy()
        last_error: Exception | None = None

        for attempt in range(1, policy.max_attempts + 1):
            step_packet = build_step_packet(
                parent=parent,
                action=action,
                payload=payload,
                source_node=self._source_node,
                reply_to=self._source_node,
            )

            try:
                response = await self._gate_client.send_to_gate(step_packet)
                _ensure_success_response(response)
                return response
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if not should_retry(attempt=attempt, error=exc, policy=policy):
                    break
                delay = policy.delay_for_attempt(attempt)
                if delay > 0:
                    await asyncio.sleep(delay)

        message = f"step execution failed for action={action!r}"
        if last_error is not None:
            raise StepExecutionError(message) from last_error
        raise StepExecutionError(message)


def _ensure_success_response(packet: TransportPacket) -> None:
    packet_type = packet.header.packet_type
    if packet_type == "failure":
        raise StepExecutionError("Gate returned a failure packet")

    payload_status = str(packet.payload.get("status", "completed")).strip().lower()
    if payload_status in {"failed", "failure", "error"}:
        raise StepExecutionError("step response payload indicates failure")
