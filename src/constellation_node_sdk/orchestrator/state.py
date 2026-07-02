from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from constellation_node_sdk.transport.packet import TransportPacket


class OrchestratorState(BaseModel):
    """
    Immutable-ish workflow state container for orchestrators.

    The orchestrator owns workflow-local accumulation state. Gate owns routing.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    root_id: UUID
    current_packet_id: UUID
    current_generation: int = Field(ge=0)
    orchestrator_node: str
    workflow_name: str | None = None
    accumulated_payload: dict[str, Any] = Field(default_factory=dict)
    step_results: tuple[dict[str, Any], ...] = ()
    completed_steps: tuple[str, ...] = ()
    failed_steps: tuple[str, ...] = ()

    @classmethod
    def from_packet(
        cls,
        *,
        packet: TransportPacket,
        orchestrator_node: str,
        workflow_name: str | None = None,
    ) -> OrchestratorState:
        return cls(
            root_id=packet.lineage.root_id,
            current_packet_id=packet.header.packet_id,
            current_generation=packet.lineage.generation,
            orchestrator_node=orchestrator_node.strip().lower(),
            workflow_name=None if workflow_name is None else workflow_name.strip(),
            accumulated_payload=dict(packet.payload),
            step_results=(),
            completed_steps=(),
            failed_steps=(),
        )

    def with_step_success(
        self,
        *,
        step_name: str,
        response_packet: TransportPacket,
        merged_payload: dict[str, Any],
    ) -> OrchestratorState:
        response_snapshot = {
            "step_name": step_name.strip().lower(),
            "packet_id": str(response_packet.header.packet_id),
            "action": response_packet.header.action,
            "payload": dict(response_packet.payload),
        }
        return self.model_copy(
            update={
                "current_packet_id": response_packet.header.packet_id,
                "current_generation": response_packet.lineage.generation,
                "accumulated_payload": dict(merged_payload),
                "step_results": self.step_results + (response_snapshot,),
                "completed_steps": self.completed_steps + (step_name.strip().lower(),),
            }
        )

    def with_step_failure(self, *, step_name: str) -> OrchestratorState:
        return self.model_copy(
            update={
                "failed_steps": self.failed_steps + (step_name.strip().lower(),),
            }
        )
