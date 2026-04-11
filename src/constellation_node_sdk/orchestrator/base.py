from __future__ import annotations

from abc import ABC, abstractmethod

from constellation_node_sdk.gate.client import GateClient
from constellation_node_sdk.transport.packet import TransportPacket

from .state import OrchestratorState
from .step_executor import StepExecutor


class BaseOrchestrator(ABC):
    """
    Base class for packet-native orchestrators.

    Orchestrators are stateful internal clients of Gate. They never resolve
    peer nodes directly and never send step packets to peer URLs.
    """

    def __init__(self, *, gate_client: GateClient, source_node: str) -> None:
        normalized_source = source_node.strip().lower()
        if not normalized_source:
            raise ValueError("source_node must not be empty")

        self._gate_client = gate_client
        self._source_node = normalized_source
        self._step_executor = StepExecutor(
            gate_client=gate_client,
            source_node=normalized_source,
        )

    @property
    def gate_client(self) -> GateClient:
        return self._gate_client

    @property
    def source_node(self) -> str:
        return self._source_node

    @property
    def step_executor(self) -> StepExecutor:
        return self._step_executor

    def initial_state(self, packet: TransportPacket) -> OrchestratorState:
        """
        Build the default initial workflow state from the inbound packet.
        """
        return OrchestratorState.from_packet(
            packet=packet,
            orchestrator_node=self._source_node,
        )

    @abstractmethod
    async def execute(self, packet: TransportPacket) -> TransportPacket:
        """
        Execute the orchestrator workflow and return a canonical response packet.
        """
