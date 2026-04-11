from __future__ import annotations

from constellation_node_sdk import register_handler
from constellation_node_sdk.gate import GateClient, get_gate_client_config_from_env
from constellation_node_sdk.orchestrator.step_executor import StepExecutor
from constellation_node_sdk.transport.packet import TransportPacket

_gate_client = GateClient(get_gate_client_config_from_env())
_step_executor = StepExecutor(gate_client=_gate_client, source_node="orchestrator")


@register_handler("full_pipeline")
async def handle_full_pipeline(_tenant: str, payload: dict, packet: TransportPacket) -> dict:
    entity_id = payload["entity_id"]

    enrich_response = await _step_executor.execute_step(
        parent=packet,
        action="enrich",
        payload={"entity_id": entity_id},
    )

    score_payload = {
        "entity_id": entity_id,
        **dict(enrich_response.payload),
    }
    score_response = await _step_executor.execute_step(
        parent=packet,
        action="score",
        payload=score_payload,
    )

    return {
        "status": "completed",
        "entity_id": entity_id,
        "enrich": enrich_response.payload,
        "score": score_response.payload,
    }
